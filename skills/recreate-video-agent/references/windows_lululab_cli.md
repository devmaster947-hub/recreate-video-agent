# Windows LuluLab Node.js CLI

The current Skill bundles `scripts/lululab_cli.mjs`. Install Node.js 20+ by its official method, then run `node scripts/lululab_cli.mjs --help` from the Skill root. The same entry works on macOS and Windows; no platform-specific LuluLab binary or `PATH` installation is needed. `LULULAB_NODE` may select an existing Node executable if `node` is not on `PATH`.

Keep the entire Skill directory in the current agent's user Skill location. The historical `scripts/install_lululab.py` helper targeted the retired platform binaries and is not part of the GitHub installation path. Do not run it for the Node.js distribution.
