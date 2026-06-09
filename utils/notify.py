import subprocess
import threading


def show(title, message, timeout=4):
    def _notify():
        xml = f"""<?xml version="1.0" encoding="utf-8"?>
<toast duration="short">
  <visual>
    <binding template="ToastText02">
      <text id="1">{title}</text>
      <text id="2">{message}</text>
    </binding>
  </visual>
</toast>"""
        ps = (
            f'$xml = @\'\n{xml}\n\'@;'
            f'$toast = [Windows.UI.Notifications.ToastNotification]::new([Windows.Data.Xml.Dom.XmlDocument]::new().LoadXml($xml));'
            f'[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("GameBot").Show($toast)'
        )
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, timeout=5)
        except Exception:
            pass
    threading.Thread(target=_notify, daemon=True).start()
