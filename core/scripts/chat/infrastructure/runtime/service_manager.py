import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path


class ServiceManager:
    """
    Manage local services via docker compose.
    searxng policy:
    - lazy start: bring up on demand;
    - keep running while the app is alive;
    - stop on app exit only if started in this session.
    """

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.compose_file = self.base_dir / "infra" / "docker-compose.services.yml"
        self.env_file = self.base_dir / ".env"
        self.project_name = "lira_services"
        self._env_map = self._read_dotenv()
        self.searxng_url = self._env("LIRA_SEARXNG_URL", "http://127.0.0.1:8080")
        self.searxng_url_ru = self._env("LIRA_SEARXNG_URL_RU", "http://127.0.0.1:8081")
        self._started_in_this_session = {"searxng": False, "searxng_ru": False}

    def _read_dotenv(self):
        data = {}
        if not self.env_file.exists():
            return data
        try:
            for line in self.env_file.read_text(encoding="utf-8").splitlines():
                raw = line.strip()
                if not raw or raw.startswith("#") or "=" not in raw:
                    continue
                k, v = raw.split("=", 1)
                data[k.strip()] = v.strip().strip('"').strip("'")
            # Simple ${VAR} substitution inside .env values
            for _ in range(2):
                for k, v in list(data.items()):
                    if "${" in v and "}" in v:
                        for ref_k, ref_v in data.items():
                            v = v.replace("${" + ref_k + "}", ref_v)
                        data[k] = v
        except Exception:
            pass
        return data

    def _env(self, key, default=""):
        val = os.environ.get(key)
        if val is not None and val != "":
            return val
        return self._env_map.get(key, default)

    def _service_name(self, route_mode="default"):
        return "searxng_ru" if route_mode == "ru" else "searxng"

    def _service_url(self, route_mode="default"):
        return self.searxng_url_ru if route_mode == "ru" else self.searxng_url

    def _masked_proxy(self, route_mode="default"):
        if route_mode != "ru":
            return "none"
        raw = self._env("HTTP_PROXY", "")
        if not raw:
            return "not_set"
        try:
            # http://user:pass@host:port -> http://user:***@host:port
            scheme, rest = raw.split("://", 1)
            if "@" not in rest:
                return f"{scheme}://***"
            creds, host = rest.split("@", 1)
            user = creds.split(":", 1)[0]
            return f"{scheme}://{user}:***@{host}"
        except Exception:
            return "***"

    def get_proxy_url(self, route_mode="default"):
        """Return proxy URL for the route; proxy is only used for ru today."""
        if route_mode != "ru":
            return ""
        return self._env("HTTP_PROXY", "")

    def _run_compose(self, args, timeout=25):
        cmd = [
            "docker",
            "compose",
            "-p",
            self.project_name,
            "-f",
            str(self.compose_file),
        ] + list(args)
        return subprocess.run(
            cmd,
            cwd=str(self.base_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def _health_check_http(self, route_mode="default", timeout=1.5):
        url = f"{self._service_url(route_mode)}/search?q=healthcheck&format=json"
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Lira2/1.0",
                    "X-Forwarded-For": "127.0.0.1",
                    "X-Real-IP": "127.0.0.1",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status != 200:
                    return False
                payload = json.loads(resp.read().decode("utf-8", errors="ignore") or "{}")
                return isinstance(payload, dict) and "results" in payload
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            # OSError: ConnectionResetError, ConnectionRefusedError, BrokenPipeError, etc. — do not crash thread
            return False

    def _container_state(self, service_name="searxng"):
        try:
            ps = self._run_compose(["ps", service_name, "--format", "json"], timeout=10)
            raw = (ps.stdout or "").strip()
            if ps.returncode != 0 or not raw:
                state = "not_running"
            else:
                try:
                    data = json.loads(raw)
                    if isinstance(data, list) and data:
                        state = data[0].get("State", "unknown")
                    elif isinstance(data, dict):
                        state = data.get("State", "unknown")
                    else:
                        state = "unknown"
                except ValueError:
                    state = "unknown"
        except Exception:
            state = "unknown"
        print(f"[SEARXNG] container_state={state}")
        return state

    def get_searxng_url(self, route_mode="default"):
        return self._service_url(route_mode)

    def ensure_searxng_running(self, route_mode="default"):
        """
        Ensure searxng is reachable.
        Returns (ok: bool, details: str).
        """
        service_name = self._service_name(route_mode)
        self._container_state(service_name)
        if self._health_check_http(route_mode=route_mode):
            print(f"[SEARXNG] service already healthy (route={route_mode})")
            return True, "searxng already healthy"

        if not self.compose_file.exists():
            return False, f"compose file not found: {self.compose_file}"

        print(
            f"[SEARXNG] starting container via docker compose "
            f"(route={route_mode}, service={service_name}, proxy={self._masked_proxy(route_mode)})"
        )
        try:
            up = self._run_compose(["up", "-d", service_name], timeout=40)
        except Exception as e:
            return False, f"compose up failed: {e}"

        if up.returncode != 0:
            err = (up.stderr or up.stdout or "").strip()
            return False, f"compose up error: {err}"

        for _ in range(8):
            if self._health_check_http(route_mode=route_mode):
                self._started_in_this_session[service_name] = True
                self._container_state(service_name)
                print(f"[SEARXNG] container started and healthy (route={route_mode})")
                return True, "searxng started by app session"
            time.sleep(0.5)
        self._container_state(service_name)
        return False, "searxng started but health check failed"

    def shutdown_managed_services(self):
        """
        Stop only services started by this app instance.
        """
        if not self.compose_file.exists():
            return
        for service_name in ("searxng_ru", "searxng"):
            if not self._started_in_this_session.get(service_name):
                self._container_state(service_name)
                print(f"[SEARXNG] skip stop: {service_name} not started by this app session")
                continue
            print(f"[SEARXNG] stopping container via docker compose ({service_name})")
            try:
                self._run_compose(["stop", service_name], timeout=20)
                self._container_state(service_name)
                print(f"[SEARXNG] container stop requested ({service_name})")
            except Exception:
                pass
