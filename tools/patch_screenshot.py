import io
import os

path = r"D:\AIQQ\panel.py"
text = open(path, encoding="utf-8").read()
start_marker = '        out_dir = os.path.join(self.root, "temp", "admin_shots")'
end_marker = "    def _napcat_login(self):"
start = text.index(start_marker)
end = text.index(end_marker, start)
new = '''        out_dir = os.path.join(self.root, "temp", "admin_shots")
        os.makedirs(out_dir, exist_ok=True)
        output = os.path.join(out_dir, "%s.png" % uuid.uuid4().hex)
        errors = []
        for headless in ("--headless=new", "--headless"):
            profile = os.path.join(out_dir, "profile-%s" % uuid.uuid4().hex)
            os.makedirs(profile, exist_ok=True)
            args = [
                browser,
                headless,
                "--disable-gpu",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-crash-reporter",
                "--hide-scrollbars",
                "--no-first-run",
                "--no-default-browser-check",
                "--window-size=1200,1400",
                "--force-device-scale-factor=0.8",
                "--user-data-dir=%s" % profile,
                "--screenshot=%s" % output,
                url,
            ]
            try:
                proc = subprocess.run(
                    args, timeout=35, stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE)
                if os.path.isfile(output) and os.path.getsize(output) > 1000:
                    return output
                detail = (proc.stderr or b"").decode("utf-8", errors="replace")[-500:]
                errors.append("截图文件为空" + (": " + detail if detail else ""))
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))
            finally:
                shutil.rmtree(profile, ignore_errors=True)
        try:
            os.remove(output)
        except OSError:
            pass
        raise PanelError("网页截图失败：%s" % ("; ".join(errors) or "未知错误"))

'''
open(path, "w", encoding="utf-8").write(text[:start] + new + text[end:])
print("patched")
