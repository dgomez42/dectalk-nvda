# DECtalk Synthesizer for NVDA

This add-on provides a DECtalk speech synthesizer driver for NVDA using the full
DECtalk runtime.

## Runtime files

Make sure the 32-bit DECtalk runtime files in the synth driver host folder (should already be there in the release):

- `addon/synthDrivers32/dectalkRuntime/IA32/DECtalk.dll`
- `addon/synthDrivers32/dectalkRuntime/IA32/dtalk_us.dic`
- `addon/synthDrivers32/dectalkRuntime/IA32/lib/dtalk_us.dll`

NVDA 2026.1 and newer use NVDA's 32-bit synth host. Older 32-bit NVDA releases
load the same driver directly.

## Compatibility

The Python driver targets NVDA 2019.3 and newer. One add-on package supports
both older 32-bit NVDA releases and 64-bit NVDA 2026.1+.
