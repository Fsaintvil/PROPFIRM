"""Monitoring — métriques, health check, structured logging

Sans dépendance externe (pas de Prometheus client).
Les métriques sont exposées en format Prometheus texte via un endpoint HTTP.

Usage:
    from engine_simple.monitoring import MetricsCollector
    metrics = MetricsCollector()
    metrics.inc("trades_total", {"symbol": "EURUSD", "direction": "BUY"})
    metrics.gauge("equity", 197500)

    # Health endpoint
    from engine_simple.monitoring import HealthServer
    server = HealthServer(port=9090, metrics=metrics)
    server.start()  # thread daemon
"""
import json
import logging
import logging.handlers
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

logger = logging.getLogger("robot.monitoring")


class MetricsCollector:
    """Collecteur de métriques — store thread-safe sans dépendance Prometheus

    Formattage Prometheus pour Grafana scraping.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._counters: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, list] = defaultdict(list)
        self._max_histogram_samples = 1000

    def inc(self, name: str, tags: dict | None = None, value: int = 1):
        with self._lock:
            key = _metric_key(name, tags)
            self._counters[name][key] += value

    def gauge(self, name: str, value: float, tags: dict | None = None):
        with self._lock:
            self._gauges[_metric_key(name, tags)] = value

    def histogram(self, name: str, value: float, tags: dict | None = None):
        with self._lock:
            key = _metric_key(name, tags)
            samples = self._histograms[name]
            samples.append((key, value, time.time()))
            if len(samples) > self._max_histogram_samples:
                self._histograms[name] = samples[-self._max_histogram_samples // 2:]

    def snapshot(self) -> dict:
        with self._lock:
            counts = {k: dict(v) for k, v in self._counters.items()}
            gauges = dict(self._gauges)
            hists = {}
            for name, samples in self._histograms.items():
                values = [s[1] for s in samples]
                if values:
                    sorted_v = sorted(values)
                    n = len(sorted_v)
                    hists[name] = {
                        "avg": round(sum(values) / n, 4),
                        "min": round(sorted_v[0], 4),
                        "max": round(sorted_v[-1], 4),
                        "p50": round(sorted_v[n // 2], 4),
                        "p95": round(sorted_v[int(n * 0.95)], 4),
                        "p99": round(sorted_v[int(n * 0.99)], 4),
                        "count": n,
                    }
            return {"counters": counts, "gauges": gauges, "histograms": hists}

    def prometheus_text(self) -> str:
        snap = self.snapshot()
        lines = []
        lines.append("# HELP robot_trades_total Nombre de trades")
        lines.append("# TYPE robot_trades_total counter")
        for name, tags_dict in snap["counters"].items():
            for tags, value in tags_dict.items():
                lines.append(f'robot_{name}{{tags="{tags}"}} {value}')
        lines.append("")
        lines.append("# HELP robot_gauges Valeurs instantanees")
        lines.append("# TYPE robot_gauges gauge")
        for tags, value in snap["gauges"].items():
            lines.append(f'robot_gauge{{name="{tags}"}} {value}')
        lines.append("")
        lines.append("# HELP robot_histogram Percentiles")
        lines.append("# TYPE robot_histogram summary")
        for name, stats in snap["histograms"].items():
            for stat, val in stats.items():
                lines.append(f'robot_histogram{{name="{name}",quantile="{stat}"}} {val}')
        return "\n".join(lines)


def _metric_key(name: str, tags: dict | None) -> str:
    if not tags:
        return name
    parts = [f"{k}={v}" for k, v in sorted(tags.items())]
    return f"{name}[{','.join(parts)}]"


class _HealthHandler(BaseHTTPRequestHandler):
    metrics_collector = None
    extra_health = None
    start_time = time.time()

    def log_message(self, format, *args):
        logger.debug(f"[HEALTH] {args[0]} {args[1]} {args[2]}")

    def _respond(self, code, data):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_GET(self):
        if self.path == "/health":
            status = "ok"
            extra = {}
            health_fn = type(self).extra_health
            if health_fn:
                extra = health_fn()
            if extra.get("status") == "error":
                status = "error"
            self._respond(200, {
                "status": status,
                "uptime_seconds": round(time.time() - self.start_time, 1),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                **extra,
            })
        elif self.path == "/metrics":
            mc = type(self).metrics_collector
            if mc:
                text = mc.prometheus_text()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4")
                self.end_headers()
                self.wfile.write(text.encode())
            else:
                self._respond(503, {"error": "metrics not available"})
        elif self.path == "/ready":
            self._respond(200, {"ready": True})
        else:
            self._respond(404, {"error": "not found"})


class HealthServer:
    """Serveur HTTP health check — thread daemon, port configurable"""

    def __init__(self, port: int = 9090, metrics: MetricsCollector | None = None,
                 health_check=None):
        self.port = port
        self.metrics = metrics
        self.health_check = health_check
        self._server = None
        self._thread = None

    def start(self):
        _HealthHandler.metrics_collector = self.metrics
        _HealthHandler.extra_health = self.health_check
        self._server = HTTPServer(("127.0.0.1", self.port), _HealthHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        logger.info(f"[MONITORING] Health endpoint: http://0.0.0.0:{self.port}/health")
        logger.info(f"[MONITORING] Metrics endpoint: http://0.0.0.0:{self.port}/metrics")
        return self

    def stop(self):
        if self._server:
            self._server.shutdown()
            logger.info("[MONITORING] Health server stopped")


def setup_structured_logging(log_dir="logs", app_name="robot"):
    """Configure le logging structuré JSON + fichier rotatif"""
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    json_handler = logging.handlers.RotatingFileHandler(
        log_path / f"{app_name}_structured.jsonl",
        maxBytes=50 * 1024 * 1024,
        backupCount=14,
        encoding="utf-8",
    )

    class JsonFormatter(logging.Formatter):
        def format(self, record):
            log_entry = {
                "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
            if hasattr(record, "extra"):
                log_entry.update(record.extra)
            if record.exc_info and record.exc_info[0]:
                log_entry["exception"] = self.formatException(record.exc_info)
            return json.dumps(log_entry)

    json_handler.setFormatter(JsonFormatter())
    json_handler.setLevel(logging.INFO)
    root_logger = logging.getLogger()
    # Éviter la duplication des handlers (cause de fuite mémoire)
    if not any(isinstance(h, logging.handlers.RotatingFileHandler)
               and h.baseFilename == json_handler.baseFilename
               for h in root_logger.handlers):
        root_logger.addHandler(json_handler)
    return json_handler


def log_extra(record, **kwargs):
    for k, v in kwargs.items():
        record.extra = getattr(record, "extra", {})
        record.extra[k] = v
