"""Reuse the token sheet repository's version API and XML validator.

Set TI_TOOLKIT_TOKENS to a compatible TI-Toolkit/tokens source checkout before
running the merger or verifier. Native extraction has no dependency on this API.
"""

import importlib.util
import os
from pathlib import Path
import sys

source = os.environ.get("TI_TOOLKIT_TOKENS")
if not source:
    raise ImportError(
        "Set TI_TOOLKIT_TOKENS to a TI-Toolkit/tokens source checkout "
        "before running locales.merge or locales.verify"
    )
entry = Path(source).expanduser().resolve() / "scripts" / "__init__.py"
if not entry.is_file():
    raise ImportError(f"Token sheet Python sources not found: {entry}")
spec = importlib.util.spec_from_file_location(
    "_ti_token_sheet_scripts", entry, submodule_search_locations=[str(entry.parent)]
)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

OsVersion = module.OsVersion
OsVersions = module.OsVersions
validate = module.validate
