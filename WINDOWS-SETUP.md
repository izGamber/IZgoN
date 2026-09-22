# IZgoN - Windows Setup Guide

## Quick Install (Windows)

### Option 1: One-Click Setup (Easiest) ⭐
Double-click **`IZgoN-Setup.cmd`**

This will:
1. ✓ Check Docker is running
2. ✓ Generate secure `.env` file with API key
3. ✓ Start Docker services
4. ✓ Open dashboard in browser

Then visit: **http://localhost:8000**

---

### Option 2: PowerShell Setup
Right-click on **`IZgoN-Setup.ps1`** → "Run with PowerShell"

Or in PowerShell:
```powershell
Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope Process
.\IZgoN-Setup.ps1
```

---

### Option 3: Manual Docker Compose
```powershell
docker compose up -d
```

Visit: **http://localhost:8000**

---

## What Gets Installed

After setup, you'll have:

```
IZgoN/
├── data/
│   └── node_states.db      # Your node data here
├── app/                    # Python FastAPI backend
├── web/                    # Web dashboard & UI
├── .env                    # Config (auto-generated with API key)
├── docker-compose.yml      # Docker setup
└── Dockerfile             # Container definition
```

---

## Access Points

| Service | URL | Purpose |
|---------|-----|---------|
| **Dashboard** | http://localhost:8000 | Web UI for monitoring |
| **API Endpoint** | http://localhost:8000/api | REST API for devices |
| **Database** | ./data/node_states.db | SQLite file |

---

## First Steps After Install

### 1. Configure Your First Node
```bash
# Example: Register a device
curl -X POST http://localhost:8000/api/nodes/device-001/sync \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "device-001",
    "temperature": 22.5,
    "humidity": 60,
    "status": "active"
  }'
```

### 2. View in Dashboard
Go to http://localhost:8000 → Check nodes and activity

### 3. Run Benchmark
```powershell
python3 benchmark.py --payload-file your-reports.json
```

### 4. Integration
Get your API key from `.env` file and integrate with your devices:
```
DATAPULSE_API_KEY=<your-key>
API_ENDPOINT=http://localhost:8000/api
```

---

## Your API Key

Your secure API key is generated automatically in `.env`:

```
DATAPULSE_API_KEY=<32-character-random-string>
```

**Keep this safe!** Use it to authenticate device requests.

---

## Useful Commands

### View Logs
```powershell
docker compose logs -f
```

### Stop Services
```powershell
docker compose down
```

### Restart
```powershell
docker compose restart
```

### Check Status
```powershell
docker compose ps
```

### Clean Everything (Start Fresh)
```powershell
docker compose down -v
```

---

## Troubleshooting

### Docker not running?
- Start Docker Desktop
- Re-run setup script

### Port 8000 already in use?
```powershell
netstat -ano | findstr :8000
taskkill /PID <PID> /F
```

### Need to rebuild?
```powershell
docker compose build --no-cache
docker compose up -d
```

### Database corrupted?
```powershell
# Backup and reset
mv data/node_states.db data/node_states.db.backup
docker compose restart
```

---

## Next Steps

- **Dashboard**: Monitor your nodes and sync activity
- **Integration**: Use API key to connect devices
- **Benchmark**: Test with your real data
- **Production**: Configure for scale with Redis
- **License**: Get commercial license at https://izgamber.github.io/IZgoN/

---

## Support

- **GitHub**: https://github.com/izGamber/IZgoN
- **Documentation**: Check README.md and BENCHMARK.md
- **Issues**: https://github.com/izGamber/IZgoN/issues

---

**Happy syncing!** 📊✨
