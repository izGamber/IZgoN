# Development Guide

## Local Setup

### Prerequisites
- Python 3.11+
- Redis 7+
- Docker & Docker Compose (optional, for containerized setup)

### Environment Setup

```bash
git clone https://github.com/izGamber/IZgoN.git
cd IZgoN
git checkout enhancement/production-ready

# Create and activate virtual environment
python -m venv venv
source venv/bin/activate  # or `venv\Scripts\activate` on Windows

# Install development dependencies
pip install -r requirements.txt
pip install pytest pytest-cov pytest-asyncio black flake8 mypy
```

### Running Locally

#### Option 1: Docker Compose (Recommended)

```bash
cp .env.example .env
docker compose up -d
```

Then access:
- Dashboard: http://localhost:8000
- API: http://localhost:8000/api/metrics

#### Option 2: Manual with Redis

```bash
# Start Redis
redis-server

# Start IzgoN
export DATAPULSE_REDIS_URL=redis://localhost:6379/0
export DATAPULSE_API_KEY=dev-local-key
uvicorn app:app --reload --host 0.0.0.0 --port 8000
```

## Testing

```bash
# Run all tests with coverage
pytest tests/ -v --cov=. --cov-report=html

# Run specific test file
pytest tests/test_engine.py -v

# Run with asyncio support
pytest tests/ -v --asyncio-mode=auto
```

## Linting & Formatting

```bash
# Format with Black
black app.py benchmark.py

# Check imports
isort --check-only app.py benchmark.py

# Lint
flake8 app.py benchmark.py

# Type check
mypy app.py --ignore-missing-imports
```

## Kubernetes Deployment

### Prerequisites
- kubectl configured
- Kubernetes cluster (1.20+)

### Deploy

```bash
kubectl apply -f k8s/redis-statefulset.yaml
kubectl apply -f k8s/izgon-deployment.yaml

# Verify
kubectl get pods
kubectl get svc

# Port forward for testing
kubectl port-forward svc/izgon 8000:8000
```

### Monitor

```bash
kubectl logs -f deployment/izgon
kubectl describe pod <pod-name>
kubectl top nodes
kubectl top pods
```

## Monitoring with Prometheus & Grafana

```bash
# Start monitoring stack
docker compose -f prometheus/docker-compose.monitoring.yml up -d

# Access Grafana
open http://localhost:3000
# Default: admin / admin

# Access Prometheus
open http://localhost:9090
```

## Benchmarking

```bash
# Local benchmark
python benchmark.py --nodes 50 --rounds 100 --change-rate 0.05

# Benchmark against running instance
python benchmark.py --payload-file your-data.jsonl --state-field state --id-field device_id
```

## CI/CD Pipeline

The project uses GitHub Actions for:
- Python 3.11 & 3.12 testing
- Linting (Black, isort, flake8)
- Type checking (mypy)
- Docker image building and pushing to GHCR

On every push to `main` or `enhancement/production-ready`:
- Tests run
- Linting checks
- Docker image builds and pushes to `ghcr.io/izgamber/izgon:latest`

## Git Workflow

```bash
# Create feature branch from enhancement/production-ready
git checkout -b feature/your-feature enhancement/production-ready

# Make changes and commit
git add .
git commit -m "feat: your change description"

# Push and create PR
git push origin feature/your-feature
```

## Troubleshooting

### Redis Connection Error
```
[izgon] WARNING: Redis is unreachable
```
Ensure Redis is running:
```bash
redis-cli ping
```

### High Memory Usage
Check metrics:
```bash
curl http://localhost:8000/api/metrics
```

Examine sync events:
```bash
docker compose exec izgon sqlite3 /data/datapulse_events.db "SELECT COUNT(*) FROM sync_events;"
```

### Tests Failing Locally but Passing in CI
Clear any local cache and test database:
```bash
rm -rf .pytest_cache datapulse_events.db
pytest tests/ -v
```

## Code Structure

- `app.py` - Main FastAPI application, all-in-one
- `benchmark.py` - Benchmarking tool
- `requirements.txt` - Python dependencies
- `Dockerfile` - Container image
- `docker-compose.yml` - Local development compose
- `.github/workflows/` - CI/CD pipelines
- `k8s/` - Kubernetes manifests
- `prometheus/` - Monitoring configuration
- `tests/` - Test suite

## Contributing

1. Fork the repository
2. Create feature branch
3. Write tests for new functionality
4. Ensure all tests pass locally
5. Format code with Black
6. Submit pull request

## Performance Tips

- Use Redis persistence (enabled by default in docker-compose.yml)
- Monitor with Prometheus for production deployments
- Scale horizontally with multiple IzgoN instances behind a load balancer (note: state is single-instance for now)
- Tune DATAPULSE_ALERT_AFTER and DATAPULSE_MAX_INTERVAL based on your use case
