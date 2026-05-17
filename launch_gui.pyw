import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from cuepoint.gui import TechnoScanApp

app = TechnoScanApp()
app.mainloop()
