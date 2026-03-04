"""
Polymarket AI Trading System - Streamlit launcher.
Keeps the console open on Windows so errors and output are visible.
"""
import subprocess
import sys
import os
import socket
import time
from pathlib import Path

# Resolve project root once (handles double-click, odd cwd, symlinks)
_PROJECT_ROOT = Path(__file__).resolve().parent


def _flush():
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass


def _keep_window_open(seconds: int = 20):
    """
    Keep window open so the user can read output.
    - On Windows with a real console: use input().
    - If stdin is closed (double-click, some launchers): input() raises EOFError;
      we fall back to time.sleep so the process does not exit immediately.
    """
    if sys.platform != "win32":
        return
    _flush()
    try:
        input("\n[Press Enter to exit...]")
    except EOFError:
        # No stdin (e.g. double-click, some IDEs): wait so user can read
        print(f"[INFO] Waiting {seconds} seconds so you can read the output...")
        _flush()
        time.sleep(seconds)
    except KeyboardInterrupt:
        pass


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return False
        except OSError:
            return True


def _wait_for_port(port: int, timeout: int = 30) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _port_in_use(port):
            return True
        time.sleep(0.5)
    return False


def _check_http_ok(host: str, port: int, timeout: int = 10) -> bool:
    """Return True if HTTP GET to host:port returns without error."""
    try:
        from urllib.request import urlopen
        url = f"http://{host}:{port}/"
        urlopen(url, timeout=timeout)
        return True
    except Exception:
        return False


def _load_dotenv():
    try:
        from dotenv import load_dotenv
        env = _PROJECT_ROOT / ".env"
        if env.exists():
            load_dotenv(env)
    except ImportError:
        pass


def main():
    _load_dotenv()

    # Run from project root so imports and paths work when launched from anywhere
    os.chdir(_PROJECT_ROOT)

    streamlit_process = None
    pause_on_exit = True

    try:
        print("=" * 70)
        print("Polymarket AI Trading System - Streamlit Launcher")
        print("=" * 70)
        print()
        _flush()

        try:
            import streamlit
            print(f"[OK] Streamlit {streamlit.__version__}")
        except ImportError:
            print("[ERROR] Streamlit is not installed.")
            print("[INFO] Run: pip install streamlit")
            _flush()
            return 1

        host = os.getenv("STREAMLIT_HOST", "127.0.0.1")
        base_port = int(os.getenv("STREAMLIT_PORT", "8501"))
        dashboard_path = _PROJECT_ROOT / "ui" / "dashboard.py"

        if not dashboard_path.exists():
            print(f"[ERROR] Dashboard not found: {dashboard_path}")
            print("[INFO] Run from the project root or use run_ui.bat")
            _flush()
            return 1

        print(f"[OK] Dashboard: {dashboard_path}")

        port = base_port
        for attempt in range(5):
            if _port_in_use(port):
                if attempt < 4:
                    print(f"[WARNING] Port {port} in use, trying {port + 1}...")
                    port += 1
                    continue
                print("[ERROR] No free port. Stop other Streamlit or set STREAMLIT_PORT.")
                _flush()
                return 1
            break

        print(f"[OK] Port {port}")
        print()
        print(f"[INFO] Starting Streamlit at http://{host}:{port}")
        print("[INFO] Keep this window open. Ctrl+C to stop.")
        print()
        _flush()

        # Set environment to skip email prompt and ensure imports work
        env = os.environ.copy()
        env["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"
        env["PYTHONPATH"] = str(_PROJECT_ROOT)
        env["PYTHONIOENCODING"] = "utf-8"  # Avoid charmap codec errors on Windows with non-ASCII
        
        args = [
            sys.executable, "-m", "streamlit", "run", str(dashboard_path),
            "--server.port", str(port),
            "--server.address", host,
            "--server.headless", "false",
            "--logger.level", "info",
            "--browser.gatherUsageStats", "false",
        ]

        try:
            streamlit_process = subprocess.Popen(
                args,
                cwd=_PROJECT_ROOT,
                env=env,
                stdin=subprocess.PIPE,
                stdout=None,
                stderr=None
            )
            # Send newline to skip email prompt
            if streamlit_process.stdin:
                streamlit_process.stdin.write(b"\n")
                streamlit_process.stdin.flush()
                streamlit_process.stdin.close()
            print(f"[OK] Process started (PID {streamlit_process.pid})")
        except Exception as e:
            print(f"[ERROR] Could not start Streamlit: {e}")
            import traceback
            traceback.print_exc()
            _flush()
            return 1

        time.sleep(1.5)

        if streamlit_process.poll() is not None:
            print("[ERROR] Streamlit exited immediately (syntax/import error in dashboard or deps).")
            print("[INFO] Run: python -m streamlit run ui/dashboard.py --server.port 8501")
            print("[INFO] to see the full traceback.")
            _flush()
            return streamlit_process.returncode or 1

        print(f"[INFO] Waiting for server on port {port}...")
        _flush()

        if not _wait_for_port(port, timeout=30):
            print("[ERROR] Server did not bind to port within 30s.")
            if streamlit_process.poll() is not None:
                print(f"[ERROR] Process exited with code {streamlit_process.returncode}")
            _flush()
            return 1

        # Give Streamlit a moment to serve HTTP
        time.sleep(2)
        if not _check_http_ok(host, port, timeout=10):
            if streamlit_process.poll() is not None:
                print(f"[ERROR] Streamlit exited with code {streamlit_process.returncode}. Run directly to see errors:")
            else:
                print("[WARNING] Server bound but not responding. If browser shows 'Connection refused', run directly to see errors:")
            print(f"  python -m streamlit run ui/dashboard.py --server.port {port} --server.address {host}")
            _flush()

        print(f"[SUCCESS] Streamlit at http://{host}:{port}")
        print("=" * 70)
        print()
        _flush()

        pause_on_exit = False

        try:
            ret = streamlit_process.wait()
            if ret != 0:
                print(f"[INFO] Streamlit exited with code {ret}")
                pause_on_exit = True
            return ret
        except KeyboardInterrupt:
            print("\n[INFO] Stopping Streamlit...")
            streamlit_process.terminate()
            try:
                streamlit_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                streamlit_process.kill()
            print("[INFO] Stopped.")
            return 0

    except KeyboardInterrupt:
        print("\n[INFO] Stopped by user")
        pause_on_exit = False
        if streamlit_process:
            try:
                streamlit_process.terminate()
                streamlit_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                streamlit_process.kill()
        return 0
    except Exception as e:
        print(f"[ERROR] {e}")
        import traceback
        traceback.print_exc()
        if streamlit_process:
            try:
                streamlit_process.terminate()
                streamlit_process.wait(timeout=5)
            except Exception:
                pass
        _flush()
        return 1
    finally:
        if pause_on_exit:
            _keep_window_open()


if __name__ == "__main__":
    sys.exit(main())
