from __future__ import annotations

from collections import OrderedDict
from ctypes import (
	POINTER,
	Structure,
	WINFUNCTYPE,
	byref,
	c_char,
	c_char_p,
	c_int,
	c_short,
	c_size_t,
	c_ssize_t,
	c_uint,
	c_void_p,
	c_wchar_p,
	cdll,
	cast,
	create_unicode_buffer,
	create_string_buffer,
	pointer,
	sizeof,
	string_at,
	windll,
)
from ctypes import wintypes
from io import BytesIO
import os
from pathlib import Path
from queue import Empty as QueueEmptyError
from queue import SimpleQueue
import re
import threading

import config
from logHandler import log
from nvwave import WavePlayer
from speech.commands import CharacterModeCommand
from speech.commands import IndexCommand
from speech.commands import PitchCommand
from speech.commands import SpeechCommand
import synthDriverHandler
from autoSettingsUtils.driverSetting import NumericDriverSetting


OWN_AUDIO_DEVICE = 1
DO_NOT_USE_AUDIO_DEVICE = 0x80000000
FORMAT = 0x00000004  # 11.025 kHz mono.
INDEX_ARRAY_SIZE = 1
SAMPLES = 2048
TTS_FORCE = 1
VOLUME_MAIN = 1
WAVE_MAPPER = 0xFFFFFFFF
DRV_RESERVED = 0x0800
DRV_QUERYFUNCTIONINSTANCEID = DRV_RESERVED + 17
DRV_QUERYFUNCTIONINSTANCEIDSIZE = DRV_RESERVED + 18
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_QUIT = 0x0012
WNDPROC = WINFUNCTYPE(c_ssize_t, c_void_p, c_uint, c_void_p, c_void_p)
STRUCTURAL_DASH_RE = re.compile(
	r"(?<=\s)[\-–—](?=\s)"
	r"|^[\-–—](?=\s)",
)
MULTIPLE_SPACES_RE = re.compile(r"[ \t]{2,}")


class WNDCLASSEXW(Structure):
	_fields_ = [
		("cbSize", wintypes.UINT),
		("style", wintypes.UINT),
		("lpfnWndProc", c_void_p),
		("cbClsExtra", c_int),
		("cbWndExtra", c_int),
		("hInstance", c_void_p),
		("hIcon", c_void_p),
		("hCursor", c_void_p),
		("hbrBackground", c_void_p),
		("lpszMenuName", c_void_p),
		("lpszClassName", c_void_p),
		("hIconSm", c_void_p),
	]


def _runtime_arch() -> str:
	return "AMD64" if sizeof(c_void_p) == 8 else "IA32"


def _runtime_dir() -> Path:
	return Path(__file__).resolve().with_name("dectalkRuntime") / _runtime_arch()


def _runtime_dll_path() -> Path:
	return _runtime_dir() / "DECtalk.dll"


def _missing_runtime_files() -> list[Path]:
	runtime = _runtime_dir()
	return [
		path
		for path in (
			runtime / "DECtalk.dll",
			runtime / "dtalk_us.dic",
			runtime / "lib" / "dtalk_us.dll",
		)
		if not path.exists()
	]


def _configure_win32_prototypes() -> None:
	kernel32 = windll.kernel32
	kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
	kernel32.GetModuleHandleW.restype = wintypes.HMODULE
	kernel32.GetCurrentThreadId.argtypes = []
	kernel32.GetCurrentThreadId.restype = wintypes.DWORD
	kernel32.CreateFileMappingA.argtypes = [
		wintypes.HANDLE,
		c_void_p,
		wintypes.DWORD,
		wintypes.DWORD,
		wintypes.DWORD,
		wintypes.LPCSTR,
	]
	kernel32.CreateFileMappingA.restype = wintypes.HANDLE
	kernel32.MapViewOfFile.argtypes = [
		wintypes.HANDLE,
		wintypes.DWORD,
		wintypes.DWORD,
		wintypes.DWORD,
		c_size_t,
	]
	kernel32.MapViewOfFile.restype = c_void_p

	ole32 = windll.ole32
	ole32.CoTaskMemFree.argtypes = [c_void_p]
	ole32.CoTaskMemFree.restype = None

	user32 = windll.user32
	user32.RegisterClassExW.argtypes = [POINTER(WNDCLASSEXW)]
	user32.RegisterClassExW.restype = wintypes.ATOM
	user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
	user32.RegisterWindowMessageW.restype = c_uint
	user32.CreateWindowExW.argtypes = [
		wintypes.DWORD,
		c_void_p,
		c_void_p,
		wintypes.DWORD,
		c_int,
		c_int,
		c_int,
		c_int,
		c_void_p,
		c_void_p,
		c_void_p,
		c_void_p,
	]
	user32.CreateWindowExW.restype = c_void_p
	user32.DefWindowProcW.argtypes = [
		c_void_p,
		c_uint,
		c_void_p,
		c_void_p,
	]
	user32.DefWindowProcW.restype = c_ssize_t
	user32.DestroyWindow.argtypes = [c_void_p]
	user32.UnregisterClassW.argtypes = [c_void_p, c_void_p]
	user32.GetMessageW.argtypes = [POINTER(wintypes.MSG), c_void_p, c_uint, c_uint]
	user32.GetMessageW.restype = c_int
	user32.TranslateMessage.argtypes = [POINTER(wintypes.MSG)]
	user32.TranslateMessage.restype = wintypes.BOOL
	user32.DispatchMessageW.argtypes = [POINTER(wintypes.MSG)]
	user32.DispatchMessageW.restype = c_ssize_t
	user32.PostMessageW.argtypes = [c_void_p, c_uint, c_void_p, c_void_p]
	user32.PostMessageW.restype = wintypes.BOOL
	user32.PostThreadMessageW.argtypes = [wintypes.DWORD, c_uint, c_void_p, c_void_p]
	user32.PostThreadMessageW.restype = wintypes.BOOL
	user32.PostQuitMessage.argtypes = [c_int]
	user32.PostQuitMessage.restype = None

	winmm = windll.winmm
	winmm.waveOutGetNumDevs.argtypes = []
	winmm.waveOutGetNumDevs.restype = c_uint
	winmm.waveOutMessage.argtypes = [c_void_p, c_uint, c_void_p, c_void_p]
	winmm.waveOutMessage.restype = c_uint


def _get_nvda_output_device() -> str:
	try:
		return config.conf["audio"]["outputDevice"]
	except Exception:
		try:
			return config.conf["speech"]["outputDevice"]
		except Exception:
			return "default"


def _waveout_id_for_endpoint(endpointId: str):
	if not endpointId or endpointId == "default":
		return WAVE_MAPPER
	winmm = windll.winmm
	for deviceId in range(winmm.waveOutGetNumDevs()):
		size = c_size_t()
		res = winmm.waveOutMessage(
			c_void_p(deviceId),
			DRV_QUERYFUNCTIONINSTANCEIDSIZE,
			cast(byref(size), c_void_p),
			None,
		)
		if res != 0 or not size.value:
			continue
		buffer = create_unicode_buffer(size.value // sizeof(wintypes.WCHAR))
		res = winmm.waveOutMessage(
			c_void_p(deviceId),
			DRV_QUERYFUNCTIONINSTANCEID,
			cast(buffer, c_void_p),
			c_void_p(size.value),
		)
		if res == 0 and buffer.value.casefold() == endpointId.casefold():
			return deviceId
	return None


def _get_dectalk_output_device() -> int:
	device = _get_nvda_output_device()
	waveoutId = _waveout_id_for_endpoint(device)
	if waveoutId is None:
		log.debugWarning("Could not map NVDA audio output device to waveOut: %s" % device)
		return WAVE_MAPPER
	return waveoutId


def _load_dectalk():
	missing = _missing_runtime_files()
	if missing:
		raise RuntimeError(
			"DECtalk runtime is incomplete for %s: %s"
			% (_runtime_arch(), ", ".join(str(path) for path in missing))
		)
	runtime = _runtime_dir()
	lib = runtime / "lib"
	os.environ["PATH"] = os.pathsep.join([str(lib), str(runtime), os.environ.get("PATH", "")])
	if hasattr(os, "add_dll_directory"):
		os.add_dll_directory(str(runtime))
		os.add_dll_directory(str(lib))
	dll = cdll.LoadLibrary(str(_runtime_dll_path()))
	_configure_dectalk_prototypes(dll)
	return dll


def _configure_dectalk_prototypes(dll) -> None:
	dll.TextToSpeechStartup.argtypes = [
		wintypes.HWND,
		POINTER(c_void_p),
		c_uint,
		c_uint,
	]
	dll.TextToSpeechStartup.restype = c_uint
	dll.TextToSpeechSpeak.argtypes = [c_void_p, c_char_p, c_uint]
	dll.TextToSpeechSpeak.restype = c_uint
	dll.TextToSpeechAddBuffer.argtypes = [c_void_p, POINTER(TTS_BUFFER_T)]
	dll.TextToSpeechAddBuffer.restype = c_uint
	dll.TextToSpeechOpenInMemory.argtypes = [c_void_p, c_uint]
	dll.TextToSpeechOpenInMemory.restype = c_uint
	dll.TextToSpeechGetCaps.argtypes = [POINTER(TTS_CAPS_T)]
	dll.TextToSpeechGetCaps.restype = c_uint
	dll.TextToSpeechReset.argtypes = [c_void_p, wintypes.BOOL]
	dll.TextToSpeechReset.restype = c_uint
	dll.TextToSpeechSetRate.argtypes = [c_void_p, wintypes.DWORD]
	dll.TextToSpeechSetRate.restype = c_uint
	dll.TextToSpeechSetSpeaker.argtypes = [c_void_p, wintypes.DWORD]
	dll.TextToSpeechSetSpeaker.restype = c_uint
	dll.TextToSpeechSetVolume.argtypes = [c_void_p, c_int, c_int]
	dll.TextToSpeechSetVolume.restype = c_uint
	try:
		dll.TextToSpeechGetSpeakerParams.argtypes = [
			c_void_p,
			c_uint,
			POINTER(POINTER(SPDEFS)),
			POINTER(POINTER(SPDEFS)),
			POINTER(POINTER(SPDEFS)),
			POINTER(POINTER(SPDEFS)),
		]
		dll.TextToSpeechGetSpeakerParams.restype = c_uint
		dll.TextToSpeechSetSpeakerParams.argtypes = [c_void_p, POINTER(SPDEFS)]
		dll.TextToSpeechSetSpeakerParams.restype = c_uint
		dll._supportsSpeakerParams = True
	except AttributeError:
		dll._supportsSpeakerParams = False
	dll.TextToSpeechPause.argtypes = [c_void_p]
	dll.TextToSpeechPause.restype = c_uint
	dll.TextToSpeechResume.argtypes = [c_void_p]
	dll.TextToSpeechResume.restype = c_uint
	dll.TextToSpeechShutdown.argtypes = [c_void_p]
	dll.TextToSpeechShutdown.restype = c_uint
	for name in (
		"TextToSpeechStartup",
		"TextToSpeechSpeak",
		"TextToSpeechAddBuffer",
		"TextToSpeechOpenInMemory",
		"TextToSpeechGetCaps",
		"TextToSpeechReset",
		"TextToSpeechSetRate",
		"TextToSpeechSetSpeaker",
		"TextToSpeechSetVolume",
	):
		getattr(dll, name).errcheck = errcheck
	if dll._supportsSpeakerParams:
		dll.TextToSpeechGetSpeakerParams.errcheck = errcheck
		dll.TextToSpeechSetSpeakerParams.errcheck = errcheck


def errcheck(res, func, args):
	if res != 0:
		raise RuntimeError("%s: code %d" % (func.__name__, res))
	return res


def clear_queue(q):
	while not q.empty():
		try:
			q.get_nowait()
		except QueueEmptyError:
			continue


def _bgExec(q, func, *args, **kwargs):
	q.put((func, args, kwargs))


class BgThread(threading.Thread):
	def __init__(self, q):
		super().__init__(daemon=True)
		self.finished = threading.Event()
		self.q = q

	def close(self):
		self.finished.set()

	def run(self):
		while not self.finished.is_set():
			try:
				func, args, kwargs = self.q.get()
				if func is None:
					self.close()
				else:
					func(*args, **kwargs)
			except Exception:
				log.error("DECtalk background thread failed", exc_info=True)


class TTS_CAPS_T(Structure):
	_fields_ = [
		("dwNumberOfLanguages", wintypes.DWORD),
		("lpLanguageParamsArray", c_void_p),
		("dwSampleRate", wintypes.DWORD),
		("dwMinimumSpeakingRate", wintypes.DWORD),
		("dwMaximumSpeakingRate", wintypes.DWORD),
		("dwNumberOfPredefinedSpeakers", wintypes.DWORD),
		("dwCharacterSet", wintypes.DWORD),
		("version", wintypes.DWORD),
	]


class TTS_INDEX_T(Structure):
	_fields_ = [
		("dwIndexValue", wintypes.DWORD),
		("dwIndexSampleNumber", wintypes.DWORD),
		("dwReserved", wintypes.DWORD),
	]


class TTS_BUFFER_T(Structure):
	_fields_ = [
		("lpData", POINTER(c_char * (SAMPLES * 2))),
		("lpPhonemeArray", c_void_p),
		("lpIndexArray", POINTER(TTS_INDEX_T * INDEX_ARRAY_SIZE)),
		("dwMaximumBufferLength", wintypes.DWORD),
		("dwMaximumNumberOfPhonemeChanges", wintypes.DWORD),
		("dwMaximumNumberOfIndexMarks", wintypes.DWORD),
		("dwBufferLength", wintypes.DWORD),
		("dwNumberOfPhonemeChanges", wintypes.DWORD),
		("dwNumberOfIndexMarks", wintypes.DWORD),
		("dwReserved", wintypes.DWORD),
	]


class SPDEFS(Structure):
	_fields_ = [
		("sex", c_short),
		("smoothness", c_short),
		("assertiveness", c_short),
		("average_pitch", c_short),
		("pitch_range", c_short),
		("breathiness", c_short),
		("richness", c_short),
		("num_fixed_samp_og", c_short),
		("laryngealization", c_short),
		("head_size", c_short),
		("formant4_res_freq", c_short),
		("formant4_bandwidth", c_short),
		("formant5_res_freq", c_short),
		("formant5_bandwidth", c_short),
		("parallel4_freq", c_short),
		("parallel5_freq", c_short),
		("gain_frication", c_short),
		("gain_aspiration", c_short),
		("gain_voicing", c_short),
		("gain_nasalization", c_short),
		("gain_cfr1", c_short),
		("gain_cfr2", c_short),
		("gain_cfr3", c_short),
		("gain_cfr4", c_short),
		("loudness", c_short),
		("spectral_tilt", c_short),
		("baseline_fall", c_short),
		("lax_breathiness", c_short),
		("quickness", c_short),
		("hat_rise", c_short),
		("stress_rise", c_short),
		("avg_glot_open", c_short),
		("avg_glot_voicd_open", c_short),
		("avg_glot_unv_open", c_short),
		("area_chink", c_short),
		("open_quo", c_short),
		("output_gain_mult", c_short),
		("junk", c_short),
		("junk1", c_short),
	]


class SynthDriver(synthDriverHandler.SynthDriver):
	name = "dectalk"
	description = _("DECtalk")
	supportedSettings = (
		synthDriverHandler.SynthDriver.VoiceSetting(),
		synthDriverHandler.SynthDriver.RateSetting(),
		synthDriverHandler.SynthDriver.PitchSetting(),
		synthDriverHandler.SynthDriver.InflectionSetting(),
		synthDriverHandler.SynthDriver.VolumeSetting(),
		NumericDriverSetting("spf", _("&SPF"), True, defaultVal=100),
	)
	supportedCommands = {
		IndexCommand,
		CharacterModeCommand,
		PitchCommand,
	}
	supportedNotifications = {
		synthDriverHandler.synthIndexReached,
		synthDriverHandler.synthDoneSpeaking,
	}
	_voices = OrderedDict()
	_voices["Paul"] = {"name": "Perfect Paul", "speaker": 0, "pitch": 122, "inflection": 100}
	_voices["Betty"] = {"name": "Beautiful Betty", "speaker": 1, "pitch": 208, "inflection": 240}
	_voices["Harry"] = {"name": "Huge Harry", "speaker": 2, "pitch": 89, "inflection": 80}
	_voices["Frank"] = {"name": "Frail Frank", "speaker": 3, "pitch": 155, "inflection": 90}
	_voices["Dennis"] = {"name": "Doctor Dennis", "speaker": 4, "pitch": 110, "inflection": 135}
	_voices["Kit"] = {"name": "Kit The Kid", "speaker": 5, "pitch": 296, "inflection": 180}
	_voices["Ursula"] = {"name": "Uppity Ursula", "speaker": 6, "pitch": 240, "inflection": 135}
	_voices["Rita"] = {"name": "Rough Rita", "speaker": 7, "pitch": 106, "inflection": 80}
	_voices["Wendy"] = {"name": "Whispering Wendy", "speaker": 8, "pitch": 200, "inflection": 175}
	minInflection = 0
	maxInflection = 250
	minPitch = 50
	maxPitch = 350
	minRate = 75
	maxRate = 650
	minSPF = 50
	maxSPF = 200

	@classmethod
	def check(cls):
		return not _missing_runtime_files()

	def __init__(self):
		_configure_win32_prototypes()
		self.dectalk = _load_dectalk()
		self.wmIndex = windll.user32.RegisterWindowMessageW("DECtalkIndexMessage")
		self.wmBuffer = windll.user32.RegisterWindowMessageW("DECtalkBufferMessage")
		self._messageWindow = None
		self._messageWindowClassAtom = None
		self._messageWindowClassName = "nvdaDtSoftWndCls%d" % id(self)
		self._messageWindowThreadId = None
		self._messageWindowReady = threading.Event()
		self._messageWindowError = None
		self._messageWindowThread = None
		self.appInstance = windll.kernel32.GetModuleHandleW(None)
		self._install_license_mapping()
		self._voice = "Paul"
		self.dt_inflection = self._voices[self._voice]["inflection"]
		self.dt_pitch = self._voices[self._voice]["pitch"]
		self.dt_rate = 180
		self.dt_spf = 100
		self.dt_volume = 1
		self.audioData = BytesIO()
		self.handle = c_void_p()
		self._useMemoryBuffers = True
		self._start_message_window_thread()
		outputDevice = WAVE_MAPPER
		cwd = os.getcwd()
		os.chdir(str(_runtime_dir()))
		try:
			self.dectalk.TextToSpeechStartup(
				self._messageWindow,
				byref(self.handle),
				outputDevice,
				DO_NOT_USE_AUDIO_DEVICE if self._useMemoryBuffers else 0,
			)
			caps = TTS_CAPS_T()
			self.dectalk.TextToSpeechGetCaps(byref(caps))
		finally:
			os.chdir(cwd)
		self.maxRate = caps.dwMaximumSpeakingRate
		self.minRate = caps.dwMinimumSpeakingRate
		self.dt_version = caps.version
		log.info("DECtalk Version %s.", self.dt_version)
		self._apply_speaker_params()
		self.mem_buffer = TTS_BUFFER_T()
		self.index_array = (TTS_INDEX_T * INDEX_ARRAY_SIZE)()
		self._buffer = create_string_buffer(SAMPLES * 2)
		self.audioQueue = SimpleQueue()
		self.player = None
		self.audio_thread = None
		if self._useMemoryBuffers:
			self.mem_buffer.lpData = pointer(self._buffer)
			self.mem_buffer.lpIndexArray = pointer(self.index_array)
			self.mem_buffer.dwMaximumBufferLength = SAMPLES * 2
			self.mem_buffer.dwMaximumNumberOfIndexMarks = INDEX_ARRAY_SIZE
			self.dectalk.TextToSpeechOpenInMemory(self.handle, FORMAT)
			self.dectalk.TextToSpeechAddBuffer(self.handle, byref(self.mem_buffer))
			self.player = WavePlayer(1, 11025, 16, outputDevice=config.conf["audio"]["outputDevice"])
			self.audio_thread = BgThread(self.audioQueue)
			self.audio_thread.start()

	def _install_license_mapping(self):
		self.mapping = windll.kernel32.CreateFileMappingA(
			wintypes.HANDLE(-1).value,
			None,
			4,
			0,
			512,
			b"a32DECtalkDllFileMap",
		)
		buf = windll.kernel32.MapViewOfFile(self.mapping, 2, 0, 0, 0)
		if not buf:
			raise RuntimeError("Could not create DECtalk license mapping")
		array = (c_char * 512).from_address(buf)
		array.value = b"\0\0\0\0r250hRm2no9fmP75YwvRhnRB81Uv6vZOTb7SdKWKae8k3BXL8U6r??3B0P91"

	def _start_message_window_thread(self):
		self._messageWindowThread = threading.Thread(
			target=self._message_window_thread,
			name="DECtalkMessageWindow",
			daemon=True,
		)
		self._messageWindowThread.start()
		if not self._messageWindowReady.wait(5):
			raise RuntimeError("Timed out creating DECtalk message window")
		if self._messageWindowError:
			raise self._messageWindowError
		log.debug("DECtalk message window ready on thread %s" % self._messageWindowThreadId)

	def _message_window_thread(self):
		try:
			self._messageWindowThreadId = windll.kernel32.GetCurrentThreadId()
			self._create_message_window()
			self._messageWindowReady.set()
			msg = wintypes.MSG()
			while True:
				res = windll.user32.GetMessageW(byref(msg), None, 0, 0)
				if res == -1:
					log.error("DECtalk message loop failed")
					break
				if res == 0:
					break
				windll.user32.TranslateMessage(byref(msg))
				windll.user32.DispatchMessageW(byref(msg))
		except Exception as e:
			self._messageWindowError = e
			self._messageWindowReady.set()
			log.error("DECtalk message window thread failed", exc_info=True)
		finally:
			if self._messageWindow:
				self._messageWindow = None
			if self._messageWindowClassAtom:
				windll.user32.UnregisterClassW(c_void_p(self._messageWindowClassAtom), self.appInstance)
				self._messageWindowClassAtom = None
			log.debug("DECtalk message window thread stopped")

	def _create_message_window(self):
		self._wndClass = WNDCLASSEXW()
		self._wndClass.cbSize = sizeof(self._wndClass)
		self._wndClass.hInstance = self.appInstance
		self._messageWindowClassNameW = c_wchar_p(self._messageWindowClassName)
		self._messageWindowTitleW = c_wchar_p("nvdaDtSoftWndCls window")
		self._wndClass.lpszClassName = cast(self._messageWindowClassNameW, c_void_p)
		self._wndProc = self._make_wndproc()
		self._wndClass.lpfnWndProc = cast(self._wndProc, c_void_p)
		self._messageWindowClassAtom = windll.user32.RegisterClassExW(byref(self._wndClass))
		self._messageWindow = windll.user32.CreateWindowExW(
			0,
			self._messageWindowClassAtom,
			cast(self._messageWindowTitleW, c_void_p),
			0,
			0,
			0,
			0,
			0,
			None,
			None,
			self.appInstance,
			None,
		)
		if not self._messageWindow:
			raise RuntimeError("Could not create DECtalk message window")

	def _make_wndproc(self):
		@WNDPROC
		def nvdaDtSoftWndProc(hwnd, msg, wParam, lParam):
			if msg == self.wmIndex:
				index = int(lParam or wParam or 0)
				if index == 32000:
					self._onDoneSpeaking()
				else:
					self._onIndexReached(index)
				return 0
			elif msg == self.wmBuffer:
				if not lParam:
					return 0
				lpBuffer = cast(lParam, POINTER(TTS_BUFFER_T))
				self.handle_buffer(lpBuffer)
				return 0
			elif msg == WM_CLOSE:
				windll.user32.DestroyWindow(hwnd)
				return 0
			elif msg == WM_DESTROY:
				windll.user32.PostQuitMessage(0)
				return 0
			return windll.user32.DefWindowProcW(hwnd, msg, wParam, lParam)

		return nvdaDtSoftWndProc

	def handle_buffer(self, lpBuffer):
		if not lpBuffer:
			log.debugWarning("DECtalk returned an empty buffer pointer")
			return
		lastmark = None
		end = False
		bufferLength = lpBuffer.contents.dwBufferLength
		if bufferLength > lpBuffer.contents.dwMaximumBufferLength:
			log.debugWarning(
				"DECtalk returned an invalid buffer length: %s > %s"
				% (bufferLength, lpBuffer.contents.dwMaximumBufferLength)
			)
			bufferLength = lpBuffer.contents.dwMaximumBufferLength
		if bufferLength and lpBuffer.contents.lpData:
			self.audioData.write(string_at(lpBuffer.contents.lpData, bufferLength))
		if lpBuffer.contents.dwBufferLength == 0:
			end = True
		lpBuffer.contents.dwBufferLength = 0
		if lpBuffer.contents.dwNumberOfIndexMarks and lpBuffer.contents.lpIndexArray:
			indexes = lpBuffer.contents.lpIndexArray.contents
			marks = lpBuffer.contents.dwNumberOfIndexMarks
			if marks > lpBuffer.contents.dwMaximumNumberOfIndexMarks:
				marks = lpBuffer.contents.dwMaximumNumberOfIndexMarks
			if marks and indexes[marks - 1].dwIndexValue == 32000:
				end = True
				if marks > 1:
					lastmark = indexes[marks - 2].dwIndexValue
				else:
					lastmark = 32000
			else:
				lastmark = indexes[marks - 1].dwIndexValue
		if self.audioData.tell() >= SAMPLES or end:
			_bgExec(self.audioQueue, self.player.feed, self.audioData.getvalue())
			self.audioData.seek(0)
			self.audioData.truncate()
		if lastmark is not None and lastmark != 32000:
			_bgExec(self.audioQueue, self._onIndexReached, lastmark)
		if end:
			_bgExec(self.audioQueue, self._onDoneSpeaking)
		self.dectalk.TextToSpeechAddBuffer(self.handle, lpBuffer)

	def speak(self, speechSequence):
		textList = [
			"[:skip none]",
			"[:mode spell off]",
			"[:punctuation none]",
			"[:period 0]",
			"[:comma 0]",
			"[:period -380]",
			"[:comma -40]",
			"[:dv ap %d]" % self.dt_pitch,
			"[:dv pr %d]" % self.dt_inflection,
		]
		for item in speechSequence:
			if isinstance(item, str):
				textList.append(self._prepare_text(item))
			elif isinstance(item, IndexCommand):
				textList.append("[:index mark %s]" % item.index)
			elif isinstance(item, CharacterModeCommand):
				textList.append("[:mode spell %s]" % ("on" if item.state else "off"))
			elif isinstance(item, PitchCommand):
				try:
					multiplier = item.multiplier
				except ZeroDivisionError:
					multiplier = (self.maxPitch - self.minPitch) // 30
				if multiplier == 1:
					textList.append("[:dv ap %d]" % self.dt_pitch)
				else:
					percent = self._paramToPercent(self.dt_pitch, self.minPitch, self.maxPitch) or 3
					value = (
						self._percentToParam(percent * multiplier, self.minPitch, self.maxPitch)
						% self.maxPitch
					)
					textList.append("[:dv ap %d]" % value)
			elif isinstance(item, SpeechCommand):
				log.debugWarning("Unsupported speech command: %s" % item)
			else:
				log.error("Unknown speech: %s" % item)
		textList.append("[:index reply 32000]")
		text = " ".join(textList).encode("iso8859-1", "ignore")
		try:
			self.dectalk.TextToSpeechSpeak(
				self.handle,
				text,
				TTS_FORCE,
			)
		except Exception:
			log.error("DECtalk failed to speak", exc_info=True)
			self._onDoneSpeaking()

	def _prepare_text(self, text):
		text = STRUCTURAL_DASH_RE.sub(" ", text)
		return MULTIPLE_SPACES_RE.sub(" ", text)

	def _send_memory_control_command(self, command):
		if not self._useMemoryBuffers or not self.handle:
			return
		try:
			self.dectalk.TextToSpeechSpeak(
				self.handle,
				command.encode("iso8859-1", "ignore"),
				TTS_FORCE,
			)
		except Exception:
			log.error("DECtalk failed to apply setting command: %s" % command, exc_info=True)

	def cancel(self):
		clear_queue(self.audioQueue)
		self.audioData.seek(0)
		self.audioData.truncate()
		if self.player:
			self.player.stop()

	def terminate(self):
		self.cancel()
		if self.handle:
			self.dectalk.TextToSpeechShutdown(self.handle)
		if self._messageWindow:
			if not windll.user32.PostMessageW(self._messageWindow, WM_CLOSE, None, None):
				log.debugWarning("Could not post close to DECtalk message window")
		elif self._messageWindowThreadId:
			windll.user32.PostThreadMessageW(self._messageWindowThreadId, WM_QUIT, None, None)
		if self._messageWindowThread:
			self._messageWindowThread.join(1)
		self.audioData.seek(0)
		self.audioData.truncate()
		if self.audio_thread:
			self.audioQueue.put((None, None, None))

	def pause(self, switch):
		if self.player:
			self.player.pause(switch)

	def _get_inflection(self):
		return self._paramToPercent(self.dt_inflection, self.minInflection, self.maxInflection)

	def _get_language(self):
		return "en_US"

	def _copy_speaker_params(self, params):
		copy = SPDEFS()
		for name, _type in SPDEFS._fields_:
			setattr(copy, name, getattr(params, name))
		return copy

	def _free_speaker_params(self, *params):
		ole32 = windll.ole32
		for param in params:
			if param:
				ole32.CoTaskMemFree(cast(param, c_void_p))

	def _get_speaker_params(self):
		current = POINTER(SPDEFS)()
		low = POINTER(SPDEFS)()
		high = POINTER(SPDEFS)()
		default = POINTER(SPDEFS)()
		self.dectalk.TextToSpeechGetSpeakerParams(
			self.handle,
			self._voices[self._voice]["speaker"],
			byref(current),
			byref(low),
			byref(high),
			byref(default),
		)
		try:
			source = current or default
			if not source:
				return None
			params = self._copy_speaker_params(source.contents)
			lowParams = self._copy_speaker_params(low.contents) if low else None
			highParams = self._copy_speaker_params(high.contents) if high else None
			return params, lowParams, highParams
		finally:
			self._free_speaker_params(current, low, high, default)

	def _clamp_speaker_param(self, value, name, lowParams, highParams):
		if lowParams is not None:
			value = max(value, getattr(lowParams, name))
		if highParams is not None:
			value = min(value, getattr(highParams, name))
		return value

	def _apply_speaker_params(self):
		if not getattr(self.dectalk, "_supportsSpeakerParams", False):
			return
		try:
			result = self._get_speaker_params()
			if result is None:
				return
			params, lowParams, highParams = result
			params.average_pitch = self._clamp_speaker_param(
				self.dt_pitch,
				"average_pitch",
				lowParams,
				highParams,
			)
			params.pitch_range = self._clamp_speaker_param(
				self.dt_inflection,
				"pitch_range",
				lowParams,
				highParams,
			)
			self.dectalk.TextToSpeechSetSpeakerParams(self.handle, byref(params))
		except Exception:
			log.error("DECtalk failed to apply speaker parameters", exc_info=True)

	def _set_inflection(self, inflection):
		self.dt_inflection = self._percentToParam(inflection, self.minInflection, self.maxInflection)
		log.info("DECtalk inflection set to %d" % self.dt_inflection)
		self._apply_speaker_params()

	def _get_pitch(self):
		return self._paramToPercent(self.dt_pitch, self.minPitch, self.maxPitch)

	def _set_pitch(self, pitch):
		self.dt_pitch = self._percentToParam(pitch, self.minPitch, self.maxPitch)
		log.info("DECtalk pitch set to %d" % self.dt_pitch)
		self._apply_speaker_params()

	def _get_rate(self):
		return self._paramToPercent(self.dt_rate, self.minRate, self.maxRate)

	def _set_rate(self, rate):
		self.dt_rate = self._percentToParam(rate, self.minRate, self.maxRate)
		log.info("DECtalk rate set to %d" % self.dt_rate)
		self.dectalk.TextToSpeechSetRate(self.handle, self.dt_rate)

	def _get_volume(self):
		return int(self.dt_volume * 100)

	def _set_volume(self, volume):
		self.dt_volume = volume / 100
		log.info("DECtalk volume set to %d" % volume)
		self.player.setVolume(all=self.dt_volume)

	def _get_spf(self):
		return self._paramToPercent(self.dt_spf, self.minSPF, self.maxSPF)

	def _set_spf(self, spf):
		self.dt_spf = self._percentToParam(spf, self.minSPF, self.maxSPF)
		log.info("DECtalk SPF set to %d" % self.dt_spf)
		self._send_memory_control_command("[:SPF %d]" % self.dt_spf)

	def _get_voice(self):
		return self._voice

	def _set_voice(self, val):
		self.dt_pitch = self._voices[val]["pitch"]
		self.dt_inflection = self._voices[val]["inflection"]
		self._voice = val
		log.info("DECtalk voice set to %s" % val)
		self.dectalk.TextToSpeechSetSpeaker(self.handle, self._voices[val]["speaker"])
		self._apply_speaker_params()

	def _getAvailableVoices(self):
		voices = OrderedDict()
		for voiceId, voice in self._voices.items():
			voices[voiceId] = synthDriverHandler.VoiceInfo(voiceId, voice["name"], language="en_US")
		return voices

	def _get_availableVoices(self):
		return self._getAvailableVoices()

	def _onIndexReached(self, index):
		synthDriverHandler.synthIndexReached.notify(synth=self, index=index)

	def _onDoneSpeaking(self):
		synthDriverHandler.synthDoneSpeaking.notify(synth=self)
