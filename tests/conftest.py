"""Put scripts/ on the import path.

The scripts are standalone entry points, not a package, and they import each other by bare
name (`from scraping import ...`). Rather than restructure the repo to make tests importable,
the tests adopt the same path the scripts already run under.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "scripts"))
