# DECtalk for NVDA
Modern DECTalk that works with 64-Bit NVDA while attempting to preserve compatibility with older NVDA versions.

The driver is architecture-aware: 32-bit NVDA loads the DECtalk driver directly,
while 64-bit NVDA 2026.1+ uses NVDA's 32-bit synth host.

## Packaging

Make sure all of the runtime files listed in `runtime-files.txt` are present, build the add-on:

```powershell
.\tools\package-addon.ps1
```

This creates one package that supports both older 32-bit NVDA releases and
64-bit NVDA 2026.1+.

### Final notes
Thanks go to the maintainers of DECTalk on GitHub, the main [DECTalk repo](https://github.com/dectalk/dectalk) is where DECTalk's source can be found, so feel free to check it out.
