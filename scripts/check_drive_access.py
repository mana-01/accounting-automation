"""Google Drive アクセス診断スクリプト

使い方:
  python scripts/check_drive_access.py

環境変数 (.env) を読み込んで以下を確認します:
  1. サービスアカウントの情報 (client_email, client_id)
  2. ドメイン全体の委任 (GOOGLE_DELEGATE_EMAIL) の設定状況
  3. 税理士共有フォルダへのアクセス可否
"""

import os
import sys
import json

# プロジェクトルートから .env を読み込む
from pathlib import Path
project_root = Path(__file__).resolve().parent.parent

# dotenv があれば使う
try:
    from dotenv import load_dotenv
    load_dotenv(project_root / ".env")
    print("✅ .env ファイルを読み込みました\n")
except ImportError:
    print("⚠️  python-dotenv が未インストールのため .env は読み込めません")
    print("   環境変数が直接設定されていれば問題ありません\n")


def get_credentials_info():
    """サービスアカウント情報を取得"""
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        creds_dict = json.loads(creds_json)
        return creds_dict, "GOOGLE_CREDENTIALS_JSON"

    creds_path = os.environ.get("GOOGLE_CREDENTIALS_PATH")
    if creds_path:
        full_path = project_root / creds_path if not os.path.isabs(creds_path) else Path(creds_path)
        if full_path.exists():
            with open(full_path) as f:
                return json.load(f), f"GOOGLE_CREDENTIALS_PATH ({full_path})"

    sa_email = os.environ.get("GOOGLE_SERVICE_ACCOUNT_EMAIL")
    if sa_email:
        return {"client_email": sa_email, "client_id": "(不明)"}, "個別の環境変数"

    return None, None


def main():
    print("=" * 60)
    print("Google Drive アクセス診断")
    print("=" * 60)

    # ===== Step 1: サービスアカウント情報 =====
    print("\n--- 1. サービスアカウント情報 ---")
    creds_dict, source = get_credentials_info()

    if not creds_dict:
        print("❌ サービスアカウントの認証情報が見つかりません")
        print("   GOOGLE_CREDENTIALS_JSON, GOOGLE_CREDENTIALS_PATH,")
        print("   または GOOGLE_SERVICE_ACCOUNT_EMAIL を設定してください")
        sys.exit(1)

    client_email = creds_dict.get("client_email", "(不明)")
    client_id = creds_dict.get("client_id", "(不明)")

    print(f"  認証情報のソース: {source}")
    print(f"  client_email:     {client_email}")
    print(f"  client_id:        {client_id}")
    print()
    print("  ☝️  共有ドライブに直接追加する場合は、上の client_email を")
    print("     共有ドライブのメンバーに「コンテンツ管理者」として追加してください")
    print()
    print("  ☝️  ドメイン全体の委任を設定する場合は、上の client_id を")
    print("     Google Workspace 管理コンソールで登録してください")

    # ===== Step 2: GOOGLE_DELEGATE_EMAIL =====
    print("\n--- 2. ドメイン全体の委任 (GOOGLE_DELEGATE_EMAIL) ---")
    delegate_email = os.environ.get("GOOGLE_DELEGATE_EMAIL", "")

    if delegate_email:
        print(f"  ✅ 設定済み: {delegate_email}")
    else:
        print("  ⚠️  未設定")
        print("  共有ドライブに SA を直接追加していない場合は、以下を .env に追加:")
        print("  GOOGLE_DELEGATE_EMAIL=mana.aizawa@iqilu-inc.com")

    # ===== Step 3: フォルダ ID の確認 =====
    print("\n--- 3. フォルダ ID ---")
    card_folder = os.environ.get("ACCOUNTANT_CARD_FOLDER_ID", "")
    bank_folder = os.environ.get("ACCOUNTANT_BANK_FOLDER_ID", "")
    drive_folder = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "")

    print(f"  GOOGLE_DRIVE_FOLDER_ID:   {drive_folder or '(未設定)'}")
    print(f"  ACCOUNTANT_CARD_FOLDER_ID: {card_folder or '(未設定)'}")
    print(f"  ACCOUNTANT_BANK_FOLDER_ID: {bank_folder or '(未設定)'}")

    # ===== Step 4: 実際のアクセステスト =====
    print("\n--- 4. Google Drive API アクセステスト ---")

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError:
        print("❌ google-auth / google-api-python-client が未インストール")
        print("   pip install google-auth google-api-python-client")
        sys.exit(1)

    scopes = ["https://www.googleapis.com/auth/drive"]

    # まず委任なしでテスト
    try:
        if "private_key" in creds_dict:
            credentials = service_account.Credentials.from_service_account_info(
                creds_dict, scopes=scopes
            )
        else:
            print("  ⚠️  private_key がないため API テストをスキップ")
            return

        service = build("drive", "v3", credentials=credentials)

        print(f"\n  [テスト A] サービスアカウント直接アクセス ({client_email})")
        _test_folder_access(service, "メインフォルダ", drive_folder)
        _test_folder_access(service, "税理士カードフォルダ", card_folder)
        _test_folder_access(service, "税理士銀行フォルダ", bank_folder)

    except Exception as e:
        print(f"  ❌ サービスアカウント認証失敗: {e}")

    # 委任ありでテスト
    if delegate_email:
        try:
            if "private_key" in creds_dict:
                credentials = service_account.Credentials.from_service_account_info(
                    creds_dict, scopes=scopes
                )
                credentials = credentials.with_subject(delegate_email)
            service = build("drive", "v3", credentials=credentials)

            print(f"\n  [テスト B] 委任アクセス ({delegate_email} として)")
            _test_folder_access(service, "メインフォルダ", drive_folder)
            _test_folder_access(service, "税理士カードフォルダ", card_folder)
            _test_folder_access(service, "税理士銀行フォルダ", bank_folder)

        except Exception as e:
            error_str = str(e)
            if "unauthorized_client" in error_str or "access_denied" in error_str:
                print(f"  ❌ 委任アクセス失敗: ドメイン全体の委任が未設定です")
                print()
                print("  🔧 修正方法:")
                print(f"     1. Google Cloud Console → サービスアカウント → {client_email}")
                print(f"        → 「ドメイン全体の委任を有効にする」をオン")
                print(f"     2. Google Workspace 管理コンソール (admin.google.com)")
                print(f"        → セキュリティ → API の制御 → ドメイン全体の委任")
                print(f"        → クライアント ID: {client_id}")
                print(f"        → スコープ: https://www.googleapis.com/auth/drive,https://www.googleapis.com/auth/spreadsheets,https://www.googleapis.com/auth/gmail.readonly")
            else:
                print(f"  ❌ 委任アクセス失敗: {e}")

    # ===== まとめ =====
    print("\n" + "=" * 60)
    print("診断完了")
    print("=" * 60)


def _test_folder_access(service, label: str, folder_id: str):
    """フォルダへのアクセスをテスト"""
    if not folder_id:
        print(f"    {label}: ⏭️  ID 未設定、スキップ")
        return

    try:
        result = service.files().get(
            fileId=folder_id,
            fields="id, name, mimeType",
            supportsAllDrives=True
        ).execute()
        name = result.get("name", "?")
        print(f"    {label}: ✅ アクセス可 → \"{name}\"")
    except Exception as e:
        error_str = str(e)
        if "404" in error_str or "notFound" in error_str:
            print(f"    {label}: ❌ 404 Not Found (アクセス権がないか、ID が無効)")
        elif "403" in error_str:
            print(f"    {label}: ❌ 403 Forbidden (権限不足)")
        else:
            print(f"    {label}: ❌ エラー: {e}")


if __name__ == "__main__":
    main()
