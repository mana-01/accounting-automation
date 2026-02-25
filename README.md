# 経理自動化ボット (Accounting Automation Bot)

Slack完結型の経理自動化システム。カード・銀行明細のCSVをアップロードするだけで、請求書との照会を自動化します。

## 機能

### 📄 CSV明細の照会
- SlackにCSVファイルをアップロードするだけで自動照会
- Sasonカードのフォーマット
- カスタムCSVフォーマットも設定可能

### 📧 請求書の自動取得
- **メール (PDF添付)**: 特定の件名パターンでPDF請求書を自動取得
- **メール (リンク)**: メール内のリンクから請求書をダウンロード
- **手動アップロード**: SlackにPDFをアップロードして保存

### 📁 Google Drive連携
- 請求書を月別フォルダ（例: `2026年1月`）に自動保存
- Slackから直接アクセス可能なリンクを生成

### 📊 Google Spreadsheet (データベース)
- サブスクリプション一覧の管理
- 照会履歴の保存
- 非エンジニアでも確認・編集可能

### ⏰ リマインド機能
- 月初の月曜日に経理作業のリマインドを自動送信
- 不足請求書のリストを通知

## セットアップ

### 1. 必要なもの
- Python 3.10以上
- Slack ワークスペース (Bot作成権限)
- Google Cloud プロジェクト (API有効化済み)

### 2. Slack App の作成

1. [Slack API](https://api.slack.com/apps) で新しいアプリを作成
2. **Socket Mode** を有効化
3. 以下の **Bot Token Scopes** を追加:
   - `chat:write`
   - `commands`
   - `files:read`
   - `files:write`
   - `app_mentions:read`
4. 以下の **Event Subscriptions** を追加:
   - `file_shared`
   - `app_mention`
   - `message.channels`
5. **Slash Commands** を追加:
   - `/accounting-help`
   - `/accounting-status`
   - `/accounting-subscriptions`
   - `/accounting-add-subscription`
   - `/accounting-invoices`
   - `/accounting-fetch-invoices`

### 3. Google Cloud の設定

1. Google Cloud Console でプロジェクトを作成
2. 以下のAPIを有効化:
   - Google Sheets API
   - Google Drive API
   - Gmail API (メール取得機能を使う場合)
3. サービスアカウントを作成し、JSONキーをダウンロード
4. Google Spreadsheet を作成し、サービスアカウントに編集権限を付与
5. Google Drive フォルダを作成し、サービスアカウントに編集権限を付与

### 4. インストール

```bash
# リポジトリをクローン
git clone <repository-url>
cd accounting-automation

# 仮想環境を作成
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 依存関係をインストール
pip install -r requirements.txt

# 環境変数を設定
cp .env.example .env
# .env を編集して必要な値を設定
```

### 5. 環境変数

`.env` ファイルを編集:

```env
# Slack
SLACK_BOT_TOKEN=xoxb-your-bot-token
SLACK_SIGNING_SECRET=your-signing-secret
SLACK_APP_TOKEN=xapp-your-app-token

# Google
GOOGLE_CREDENTIALS_PATH=./credentials.json
GOOGLE_SPREADSHEET_ID=your-spreadsheet-id
GOOGLE_DRIVE_FOLDER_ID=your-folder-id
GMAIL_USER_EMAIL=your-email@gmail.com  # Gmail取得機能を使う場合

# Slack通知チャンネル
SLACK_NOTIFICATION_CHANNEL=#accounting
```

### 6. 起動

```bash
python -m src
```

## 使い方

### CSV明細の照会

1. カード会社/銀行のWebサイトから明細CSVをダウンロード
2. Slackの指定チャンネルにCSVファイルをアップロード
3. ボットが自動で照会を実行し、結果を報告

### サブスクリプションの登録

```
/accounting-add-subscription
```

モーダルが開くので、以下を入力:
- サービス名 (例: AWS)
- ベンダー名 (例: Amazon Web Services)
- 金額
- 請求サイクル (月次/年次/四半期)
- 支払方法 (カード/銀行振込)
- 請求書取得方法

### 請求書のアップロード

1. PDFファイルをSlackにアップロード
2. 「請求書として保存」ボタンをクリック
3. サブスクリプションと金額を選択
4. Google Driveに自動保存

### 状況確認

```
/accounting-status
```

今月の照会状況、請求書の取得状況を確認できます。

## プロジェクト構造

```
accounting-automation/
├── src/
│   ├── __init__.py
│   ├── __main__.py
│   ├── app.py              # メインアプリケーション
│   ├── models/
│   │   └── __init__.py     # データモデル定義
│   ├── services/
│   │   ├── __init__.py
│   │   ├── spreadsheet.py  # Google Spreadsheet連携
│   │   ├── drive.py        # Google Drive連携
│   │   ├── gmail.py        # Gmail連携
│   │   ├── csv_parser.py   # CSV解析
│   │   └── reconciliation.py # 照会ロジック
│   ├── handlers/
│   │   ├── __init__.py
│   │   ├── commands.py     # スラッシュコマンド
│   │   ├── events.py       # イベントハンドラー
│   │   └── actions.py      # インタラクティブアクション
│   └── utils/
│       ├── __init__.py
│       └── scheduler.py    # 定期タスク
├── tests/
├── requirements.txt
├── pyproject.toml
├── .env.example
└── README.md
```

## Google Spreadsheet の構造

| シート名 | 内容 |
|---------|------|
| `subscriptions` | 取得ルール（メール自動取得 / 手動確認 / スキャン） |
| `invoices` | 請求書一覧 |
| `reconciliation_history` | 照会履歴 |

## 対応CSVフォーマット

- 楽天カード (`rakuten_card`)
- SMBCカード (`smbc_card`)
- 三菱UFJ銀行 (`mufg_bank`)
- 汎用フォーマット (`generic`)

その他のフォーマットは自動検出を試みます。

## ライセンス

MIT
