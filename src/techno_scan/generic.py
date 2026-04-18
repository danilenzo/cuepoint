from pathlib import Path

BASE_PATH = Path(__file__).resolve().parent.parent.parent

RA = "https://ra.co"
URL = "https://ra.co/graphql"
CITY_URL = RA + "/events/uk/london"
OUTPUT_PATH = str(BASE_PATH / "output") + "/"
