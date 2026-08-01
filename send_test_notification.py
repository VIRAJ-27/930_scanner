from dotenv import load_dotenv

from scanner930.notifications import NotificationService


def main() -> None:
    load_dotenv()
    notifier = NotificationService()
    print(
        "Telegram:", "configured" if notifier.telegram_enabled else "not configured"
    )
    print("Email:", "configured" if notifier.email_enabled else "not configured")
    notifier.send_text("Scanner930 notification test successful.")
    notifier.close()
    if notifier.last_error:
        raise RuntimeError(notifier.last_error)


if __name__ == "__main__":
    main()
