"""Chatwork 通知テストスクリプト

使い方:
  python scripts/test_chatwork_notify.py

環境変数 (.env) を読み込んで以下を実行します:
  1. Chatwork API設定の確認
  2. テストメッセージの組み立て・表示
  3. 実際にChatworkへ送信
"""

import os
import sys
from pathlib import Path

# プロジェクトルートから .env を読み込む
project_root = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv
    load_dotenv(project_root / ".env")
    print("✅ .env ファイルを読み込みました\n")
except ImportError:
    print("⚠️  python-dotenv が未インストールのため .env は読み込めません")
    print("   環境変数が直接設定されていれば問題ありません\n")


def main():
    print("=" * 60)
    print("Chatwork 通知テスト")
    print("=" * 60)

    # ===== Step 1: 設定確認 =====
    print("\n--- 1. Chatwork API 設定確認 ---")

    api_token = os.getenv("CHATWORK_API_TOKEN", "")
    room_id = os.getenv("CHATWORK_ROOM_ID", "")
    accountant_id = os.getenv("CHATWORK_ACCOUNTANT_ACCOUNT_ID", "")

    print(f"  CHATWORK_API_TOKEN:              {'✅ 設定済み' if api_token else '❌ 未設定'}")
    print(f"  CHATWORK_ROOM_ID:                {room_id if room_id else '❌ 未設定'}")
    print(f"  CHATWORK_ACCOUNTANT_ACCOUNT_ID:  {accountant_id if accountant_id else '⚠️  未設定 (To通知なし)'}")

    if not api_token or not room_id:
        print("\n❌ CHATWORK_API_TOKEN と CHATWORK_ROOM_ID を .env に設定してください")
        print("   .env.example を参考にしてください")
        sys.exit(1)

    # ===== Step 2: メッセージ組み立て =====
    print("\n--- 2. 送信メッセージのプレビュー ---")

    # テスト用のサンプルデータ
    period = "2026年2月"
    card_count = 3
    bank_count = 2
    card_folder_url = "https://drive.google.com/drive/folders/sample-card"
    bank_folder_url = "https://drive.google.com/drive/folders/sample-bank"

    # ChatworkService と同じロジックでメッセージ組み立て
    to_line = ""
    if accountant_id:
        to_line = f"[To:{accountant_id}]\n"

    lines = [
        to_line + f"[info][title]【テスト】{period} 請求書共有のお知らせ[/title]",
        "⚠️ これはテスト送信です。実際の請求書共有ではありません。",
        "",
        f"{period}分の請求書をGoogle Driveの共有フォルダにアップロードいたしました。",
        f"ご確認をお願いいたします。",
        "",
    ]

    if card_count > 0:
        lines.append(f"■ クレジットカード: {card_count}件")
        if card_folder_url:
            lines.append(f"  {card_folder_url}")

    if bank_count > 0:
        lines.append(f"■ 銀行振込: {bank_count}件")
        if bank_folder_url:
            lines.append(f"  {bank_folder_url}")

    total = card_count + bank_count
    lines.append(f"\n合計: {total}件")
    lines.append("[/info]")

    message = "\n".join(lines)

    print()
    print(message)
    print()

    # ===== Step 3: 送信確認 =====
    print("--- 3. 送信 ---")
    answer = input("上記のメッセージをChatworkに送信しますか？ (y/N): ").strip().lower()

    if answer != "y":
        print("キャンセルしました")
        sys.exit(0)

    # ===== Step 4: 送信実行 =====
    print("\n送信中...")

    # src をインポートパスに追加
    sys.path.insert(0, str(project_root))

    from src.services.chatwork import ChatworkService

    service = ChatworkService()
    try:
        result = service.send_message(message)
        message_id = result.get("message_id", "(不明)")
        print(f"\n✅ 送信成功！")
        print(f"   message_id: {message_id}")
        print(f"   ルームID:   {room_id}")
        if accountant_id:
            print(f"   To通知:     アカウントID {accountant_id} にプッシュ通知が届きます")
        else:
            print(f"   To通知:     なし（CHATWORK_ACCOUNTANT_ACCOUNT_ID 未設定）")
    except Exception as e:
        print(f"\n❌ 送信失敗: {e}")
        if "401" in str(e):
            print("   → APIトークンが無効です。Chatwork管理画面で確認してください。")
        elif "403" in str(e):
            print("   → このルームへの投稿権限がありません。")
        elif "404" in str(e):
            print("   → ルームIDが無効です。正しいルームIDを設定してください。")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("テスト完了")
    print("=" * 60)


if __name__ == "__main__":
    main()
