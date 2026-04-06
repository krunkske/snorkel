# POI Claimer Dashboard

Browser dashboard that auto-claims selected POIs every 15 minutes.

## Folder structure

```
poi-dashboard/
├── app/
│   ├── main.py
│   └── templates/index.html
├── data/                        ← mount this into the container
│   ├── rows.json                ← exported from fetch_database.py
│   └── images/
│       ├── De Zille/
│       │   ├── day/
│       │   │   ├── photo1.jpg
│       │   │   └── photo2.jpg
│       │   └── night/
│       │       └── photo1.jpg
│       └── Floralux/
│           ├── day/
│           └── night/
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

Image folders are named **exactly** after the POI name (case-sensitive).
If a folder or images don't exist the last server image is used instead.

## Run with Docker

```bash
# 1. Build and start
docker compose up --build

# 2. Open dashboard
open http://localhost:5000

# 3. Stop
docker compose down
```

## Run without Docker

```bash
pip install -r requirements.txt

# Put rows.json and images/ next to this file, then:
ROWS_FILE=data/rows.json IMAGES_DIR=data/images python app/main.py
```

## Updating credentials

When the syncToken expires (~90 days) update it in docker-compose.yml:

```yaml
SYNC_TOKEN: "eyJ..."
APP_VERSION: "1.000xxx"
CLIENT_ID:   "..."
BUILD:       "..."
```

## Environment variables

| Variable                 | Default          | Description                              |
|--------------------------|------------------|------------------------------------------|
| `NEW_OWNER`              | Chiro Oostham    | Team name to assign as owner             |
| `CLAIM_INTERVAL_MINUTES` | 15               | How often to run the claim cycle         |
| `DELAY_MIN`              | 2                | Min seconds between individual POI calls |
| `DELAY_MAX`              | 8                | Max seconds between individual POI calls |
| `SYNC_TOKEN`             | (hardcoded)      | JWT from HAR export                      |
| `APP_VERSION`            | 1.000429         | AppSheet app version from HAR            |
| `CLIENT_ID`              | (hardcoded)      | Client ID from HAR                       |
| `BUILD`                  | (hardcoded)      | Build hash from HAR                      |
| `ROWS_FILE`              | /data/rows.json  | Path to rows.json inside container       |
| `IMAGES_DIR`             | /data/images     | Path to images folder inside container   |
