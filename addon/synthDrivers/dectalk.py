from __future__ import annotations

from ctypes import c_void_p, sizeof
from pathlib import Path


if sizeof(c_void_p) == 8:
	from _bridge.clients.synthDriverHost32.synthDriver import SynthDriverProxy32

	class SynthDriver(SynthDriverProxy32):
		name = "dectalk"
		description = _("DECtalk")
		synthDriver32Path = str(Path(__file__).resolve().parent.parent / "synthDrivers32")
		synthDriver32Name = "dectalk"

		def _get_spf(self):
			return self._remoteService.getParam("spf")

		def _set_spf(self, value):
			self._remoteService.setParam("spf", value)

		def _get_inflection(self):
			return self._remoteService.getParam("inflection")

		def _set_inflection(self, value):
			self._remoteService.setParam("inflection", value)

else:
	import importlib.util

	_driverPath = Path(__file__).resolve().parent.parent / "synthDrivers32" / "dectalk.py"
	_spec = importlib.util.spec_from_file_location("_dectalk32_direct", _driverPath)
	if _spec is None or _spec.loader is None:
		raise RuntimeError("Could not load DECtalk 32-bit driver from %s" % _driverPath)
	_module = importlib.util.module_from_spec(_spec)
	_spec.loader.exec_module(_module)
	SynthDriver = _module.SynthDriver
