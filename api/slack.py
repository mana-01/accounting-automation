"""Vercel serverless function entry point for Slack."""

import os
import json
import re
from flask import Flask, request, Response
from slack_bolt import App as SlackApp
from slack_bolt.adapter.flask import SlackRequestHandler
from slack_sdk import WebClient

# Flask app
app = Flask(__name__)

# Slack App
slack_app = SlackApp(
    token=os.environ.get("SLACK_BOT_TOKEN"),
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET"),
    process_before_response=True,
)

slack_handler = SlackRequestHandler(slack_app)


# === Slack Commands ===

@slack_app.command("/accounting-help")
def handle_help(ack, respond):
    """ヘルプを表示"""
    ack()
    respond({
        "response_type": "ephemeral",
        "text": """*経理自動化Bot ヘルプ*

*コマンド一覧:*
• `/accounting-help` - このヘルプを表示
• `/accounting-status` - 今月の経理状況を確認
• `/accounting-fetch-invoices [期間]` - メールから請求書を自動取得
• `/accounting-add-email-rule` - メール取得ルールを追加
• `/accounting-email-rules` - ルール一覧・削除
• `/accounting-invoices` - 取得済み請求書一覧
• `/accounting-reconcile [期間]` - CSV照会を実行

*期間指定の例:*
• `202602` - 2026年2月分のみ
• `202509~202601` - 2025年9月〜2026年1月

*使い方:*
1. `/accounting-add-email-rule` でメール取得ルールを設定
2. `/accounting-fetch-invoices 202602` でメールから請求書を取得
3. CSVファイルをアップロード（Saisonまたは銀行）
4. `/accounting-reconcile 202602` で照会
5. 不足している請求書がリストアップされます

*フォルダ構造:*
📁 2026年2月/
├── 📁 202602_クレジット/
└── 📁 202602_銀行振込/"""
    })


@slack_app.command("/accounting-status")
def handle_status(ack, respond):
    """状況を表示"""
    ack()

    try:
        from api.services.invoice_fetcher import invoice_fetcher

        # Spreadsheetから請求書数を取得
        result = invoice_fetcher.sheets.spreadsheets().values().get(
            spreadsheetId=invoice_fetcher.spreadsheet_id,
            range="invoices!A2:H1000"
        ).execute()
        invoices = result.get("values", [])

        # ルール数を取得
        rules_result = invoice_fetcher.sheets.spreadsheets().values().get(
            spreadsheetId=invoice_fetcher.spreadsheet_id,
            range="email_rules!A2:E100"
        ).execute()
        rules = rules_result.get("values", [])

        respond({
            "response_type": "ephemeral",
            "text": f"""📊 *経理状況*

• メール取得ルール: {len(rules)}件
• 取得済み請求書: {len(invoices)}件

`/accounting-fetch-invoices` でメールから請求書を取得できます。"""
        })
    except Exception as e:
        respond({
            "response_type": "ephemeral",
            "text": f"📊 *経理状況*\n\nデータ取得中にエラー: {str(e)}"
        })


@slack_app.command("/accounting-add-email-rule")
def handle_add_email_rule(ack, client, body):
    """メール取得ルール追加モーダル"""
    ack()
    client.views_open(
        trigger_id=body["trigger_id"],
        view={
            "type": "modal",
            "callback_id": "add_email_rule_modal",
            "title": {"type": "plain_text", "text": "メール取得ルール追加"},
            "submit": {"type": "plain_text", "text": "追加"},
            "close": {"type": "plain_text", "text": "キャンセル"},
            "blocks": [
                {
                    "type": "input",
                    "block_id": "name_block",
                    "label": {"type": "plain_text", "text": "ルール名（サービス名）"},
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "name_input",
                        "placeholder": {"type": "plain_text", "text": "例: AWS, GitHub, Notion"}
                    }
                },
                {
                    "type": "input",
                    "block_id": "sender_block",
                    "label": {"type": "plain_text", "text": "送信者メールアドレス"},
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "sender_input",
                        "placeholder": {"type": "plain_text", "text": "例: billing@aws.amazon.com"}
                    }
                },
                {
                    "type": "input",
                    "block_id": "subject_block",
                    "label": {"type": "plain_text", "text": "件名キーワード"},
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "subject_input",
                        "placeholder": {"type": "plain_text", "text": "例: Invoice, 請求書, ご利用明細"}
                    }
                },
                {
                    "type": "input",
                    "block_id": "type_block",
                    "label": {"type": "plain_text", "text": "取得タイプ"},
                    "element": {
                        "type": "static_select",
                        "action_id": "type_select",
                        "options": [
                            {
                                "text": {"type": "plain_text", "text": "PDF添付ファイル"},
                                "value": "attachment"
                            },
                            {
                                "text": {"type": "plain_text", "text": "メール内リンク"},
                                "value": "link"
                            }
                        ],
                        "initial_option": {
                            "text": {"type": "plain_text", "text": "PDF添付ファイル"},
                            "value": "attachment"
                        }
                    }
                }
            ]
        }
    )


@slack_app.view("add_email_rule_modal")
def handle_add_email_rule_submission(ack, body, client, view):
    """メール取得ルール追加の処理"""
    ack()

    try:
        values = view["state"]["values"]
        name = values["name_block"]["name_input"]["value"]
        sender = values["sender_block"]["sender_input"]["value"]
        subject = values["subject_block"]["subject_input"]["value"]
        fetch_type = values["type_block"]["type_select"]["selected_option"]["value"]

        from api.services.invoice_fetcher import invoice_fetcher

        # Spreadsheetに追加
        row = [name, sender, subject, fetch_type, ""]
        invoice_fetcher.sheets.spreadsheets().values().append(
            spreadsheetId=invoice_fetcher.spreadsheet_id,
            range="email_rules!A:E",
            valueInputOption="USER_ENTERED",
            body={"values": [row]}
        ).execute()

        user_id = body["user"]["id"]
        client.chat_postMessage(
            channel=user_id,
            text=f"""✅ メール取得ルールを追加しました！

• *ルール名*: {name}
• *送信者*: {sender}
• *件名キーワード*: {subject}
• *取得タイプ*: {"PDF添付" if fetch_type == "attachment" else "リンク"}

`/accounting-fetch-invoices` で請求書を取得できます。"""
        )

    except Exception as e:
        user_id = body["user"]["id"]
        client.chat_postMessage(
            channel=user_id,
            text=f"❌ ルール追加エラー: {str(e)}"
        )


@slack_app.command("/accounting-fetch-invoices")
def handle_fetch_invoices(ack, respond, body, client):
    """メールから請求書を自動取得"""
    ack()

    # 引数から期間を取得
    text = body.get("text", "").strip()
    user_id = body.get("user_id")

    # 即座に応答（タイムアウト防止）
    if text:
        respond({
            "response_type": "ephemeral",
            "text": f"📥 期間 `{text}` の請求書を取得中...\nフォルダを作成しています...\n完了したらお知らせします。"
        })
    else:
        respond({
            "response_type": "ephemeral",
            "text": "📥 過去30日のメールから請求書を取得中...\n完了したらお知らせします。"
        })

    # 非同期で処理を実行
    import threading

    def process_invoices():
        try:
            from api.services.invoice_fetcher import invoice_fetcher

            if text:
                results = invoice_fetcher.fetch_invoices_by_period(text)

                periods_info = ""
                for p in results.get("periods_created", [])[:5]:
                    periods_info += f"\n• {p['period']}"

                if results["errors"]:
                    error_text = "\n".join(results["errors"][:3])
                    client.chat_postMessage(
                        channel=user_id,
                        text=f"""⚠️ *請求書取得完了（エラーあり）*

*作成したフォルダ:*{periods_info}

• 処理したメール: {results['processed']}件
• 保存した請求書: {results['saved']}件
• スキップ（重複）: {results.get('skipped', 0)}件

*エラー:*
{error_text}"""
                    )
                else:
                    invoice_list = ""
                    for inv in results["invoices"][:5]:
                        invoice_list += f"\n• {inv.get('vendor', '不明')} ({inv.get('date', '')})"

                    skipped_info = f"\n• スキップ（重複）: {results.get('skipped', 0)}件" if results.get('skipped', 0) > 0 else ""

                    client.chat_postMessage(
                        channel=user_id,
                        text=f"""✅ *請求書取得完了*

*作成したフォルダ:*{periods_info}

• 処理したメール: {results['processed']}件
• 保存した請求書: {results['saved']}件{skipped_info}
{invoice_list if invoice_list else ''}

Google Driveに保存されました。
次にCSVファイルをアップロードしてください。"""
                    )
            else:
                results = invoice_fetcher.fetch_invoices(days_back=30)

                if results["errors"]:
                    error_text = "\n".join(results["errors"][:3])
                    client.chat_postMessage(
                        channel=user_id,
                        text=f"""⚠️ *請求書取得完了（エラーあり）*

• 処理したメール: {results['processed']}件
• 保存した請求書: {results['saved']}件

*エラー:*
{error_text}

💡 期間指定: `/accounting-fetch-invoices 202602` または `202509~202601`"""
                    )
                else:
                    invoice_list = ""
                    for inv in results["invoices"][:5]:
                        invoice_list += f"\n• {inv.get('vendor', '不明')} ({inv.get('date', '')})"

                    client.chat_postMessage(
                        channel=user_id,
                        text=f"""✅ *請求書取得完了*

• 処理したメール: {results['processed']}件
• 保存した請求書: {results['saved']}件
{invoice_list if invoice_list else ''}

💡 期間指定: `/accounting-fetch-invoices 202602` または `202509~202601`"""
                    )

        except Exception as e:
            client.chat_postMessage(
                channel=user_id,
                text=f"❌ エラー: {str(e)}\n\n期間形式: `202602` または `202509~202601`"
            )

    thread = threading.Thread(target=process_invoices)
    thread.start()


@slack_app.command("/accounting-invoices")
def handle_invoices(ack, respond):
    """請求書一覧"""
    ack()

    try:
        from api.services.invoice_fetcher import invoice_fetcher

        result = invoice_fetcher.sheets.spreadsheets().values().get(
            spreadsheetId=invoice_fetcher.spreadsheet_id,
            range="invoices!A2:H100"
        ).execute()

        rows = result.get("values", [])

        if not rows:
            respond({
                "response_type": "ephemeral",
                "text": "📄 *請求書一覧*\n\nまだ請求書がありません。`/accounting-fetch-invoices` で取得してください。"
            })
            return

        invoice_list = ""
        for row in rows[-10:]:  # 最新10件
            vendor = row[1] if len(row) > 1 else "不明"
            amount = row[2] if len(row) > 2 else "-"
            date = row[3] if len(row) > 3 else ""
            url = row[5] if len(row) > 5 else ""

            if url:
                invoice_list += f"\n• <{url}|{vendor}> - {date} (¥{amount})"
            else:
                invoice_list += f"\n• {vendor} - {date} (¥{amount})"

        respond({
            "response_type": "ephemeral",
            "text": f"📄 *請求書一覧（最新10件）*\n{invoice_list}"
        })

    except Exception as e:
        respond({
            "response_type": "ephemeral",
            "text": f"❌ エラー: {str(e)}"
        })


@slack_app.command("/accounting-email-rules")
def handle_email_rules(ack, respond):
    """メール取得ルール一覧"""
    ack()

    try:
        from api.services.invoice_fetcher import invoice_fetcher

        result = invoice_fetcher.sheets.spreadsheets().values().get(
            spreadsheetId=invoice_fetcher.spreadsheet_id,
            range="email_rules!A2:E100"
        ).execute()

        rows = result.get("values", [])

        if not rows:
            respond({
                "response_type": "ephemeral",
                "text": "📧 *メール取得ルール一覧*\n\nルールがありません。`/accounting-add-email-rule` で追加してください。"
            })
            return

        blocks = [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "📧 *メール取得ルール一覧*"}
            },
            {"type": "divider"}
        ]

        for i, row in enumerate(rows):
            name = row[0] if len(row) > 0 else "不明"
            sender = row[1] if len(row) > 1 else "-"
            subject = row[2] if len(row) > 2 else "-"
            fetch_type = row[3] if len(row) > 3 else "attachment"
            type_text = "PDF添付" if fetch_type == "attachment" else "リンク"

            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{name}*\n送信者: `{sender}`\n件名: `{subject}`\nタイプ: {type_text}"
                },
                "accessory": {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "削除"},
                    "style": "danger",
                    "action_id": "delete_email_rule",
                    "value": str(i + 2)  # 行番号（ヘッダー分+1、0始まり分+1）
                }
            })

        respond({
            "response_type": "ephemeral",
            "blocks": blocks
        })

    except Exception as e:
        respond({
            "response_type": "ephemeral",
            "text": f"❌ エラー: {str(e)}"
        })


@slack_app.action("delete_email_rule")
def handle_delete_email_rule(ack, body, client):
    """メール取得ルールを削除"""
    ack()

    row_num = body["actions"][0]["value"]
    user_id = body["user"]["id"]

    try:
        from api.services.invoice_fetcher import invoice_fetcher

        # 該当行をクリア
        invoice_fetcher.sheets.spreadsheets().values().clear(
            spreadsheetId=invoice_fetcher.spreadsheet_id,
            range=f"email_rules!A{row_num}:E{row_num}"
        ).execute()

        client.chat_postMessage(
            channel=user_id,
            text="✅ ルールを削除しました。`/accounting-email-rules` で確認してください。"
        )

    except Exception as e:
        client.chat_postMessage(
            channel=user_id,
            text=f"❌ 削除エラー: {str(e)}"
        )


@slack_app.command("/accounting-subscriptions")
def handle_subscriptions(ack, respond):
    """サブスク一覧（使わないが互換性のため残す）"""
    ack()
    respond({
        "response_type": "ephemeral",
        "text": "📋 この機能は `/accounting-email-rules` に置き換えられました。"
    })


@slack_app.command("/accounting-add-subscription")
def handle_add_subscription(ack, respond):
    """サブスク登録（使わないが互換性のため残す）"""
    ack()
    respond({
        "response_type": "ephemeral",
        "text": "📋 この機能は `/accounting-add-email-rule` に置き換えられました。"
    })


# === Slack Events ===

def _send_file_buttons(client, channel_id: str, file_id: str, file_name: str, file_type: str):
    """ファイル検出時にボタンを送信する共通関数"""
    file_id_str = str(file_id)

    if file_type == "csv" or file_name.endswith(".csv"):
        client.chat_postMessage(
            channel=channel_id,
            text=f"📄 CSV ファイル `{file_name}` を検出しました。\nCSVの種類を選択してください。",
            blocks=[
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"📄 CSV ファイル `{file_name}` を検出しました。\nCSVの種類を選択してください。"}
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Saisonカード"},
                            "style": "primary",
                            "action_id": "process_csv_saison",
                            "value": file_id_str
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "銀行口座"},
                            "action_id": "process_csv_bank",
                            "value": file_id_str
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "スキップ"},
                            "action_id": "skip_file",
                            "value": file_id_str
                        }
                    ]
                }
            ]
        )
    elif file_type == "pdf" or file_name.endswith(".pdf"):
        client.chat_postMessage(
            channel=channel_id,
            text=f"📎 PDF ファイル `{file_name}` を検出しました。\n保存先を選択してください。",
            blocks=[
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"📎 PDF ファイル `{file_name}` を検出しました。\n保存先を選択してください。"}
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "クレジット請求書"},
                            "style": "primary",
                            "action_id": "save_invoice_credit",
                            "value": file_id_str
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "銀行振込請求書"},
                            "action_id": "save_invoice_bank",
                            "value": file_id_str
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "スキップ"},
                            "action_id": "skip_file",
                            "value": file_id_str
                        }
                    ]
                }
            ]
        )


@slack_app.event("file_shared")
def handle_file_shared(event, client, say):
    """ファイル共有イベント"""
    file_id = event.get("file_id")
    channel_id = event.get("channel_id")
    user_id = event.get("user_id")

    print(f"file_shared event: file_id={file_id}, channel_id={channel_id}, user_id={user_id}")

    try:
        file_info = client.files_info(file=file_id)
        file_data = file_info["file"]
        file_name = file_data.get("name", "")
        file_type = file_data.get("filetype", "")

        if not channel_id:
            channels = file_data.get("channels", [])
            if channels:
                channel_id = channels[0]
            elif file_data.get("ims"):
                channel_id = file_data["ims"][0]
            elif user_id:
                channel_id = user_id

        if not channel_id:
            print(f"No channel_id found for file {file_id}")
            return

        _send_file_buttons(client, channel_id, file_id, file_name, file_type)

    except Exception as e:
        print(f"Error handling file_shared: {e}")
        import traceback
        traceback.print_exc()


@slack_app.event("message")
def handle_message(event, client):
    """メッセージイベント（ファイルアップロード検知用）"""
    # ファイル添付があるメッセージのみ処理
    files = event.get("files")
    if not files:
        return

    # Bot自身のメッセージは無視
    if event.get("bot_id"):
        return

    channel_id = event.get("channel")
    subtype = event.get("subtype", "")

    print(f"message event with files: channel={channel_id}, subtype={subtype}, files={len(files)}")

    for f in files:
        file_id = f.get("id")
        file_name = f.get("name", "")
        file_type = f.get("filetype", "")

        if not file_id:
            continue

        # CSV or PDF のみ処理
        is_csv = file_type == "csv" or file_name.endswith(".csv")
        is_pdf = file_type == "pdf" or file_name.endswith(".pdf")

        if is_csv or is_pdf:
            try:
                _send_file_buttons(client, channel_id, file_id, file_name, file_type)
            except Exception as e:
                print(f"Error handling file in message: {e}")
                import traceback
                traceback.print_exc()


@slack_app.action("process_csv_saison")
def handle_process_csv_saison(ack, body, client):
    """SaisonカードCSVを処理"""
    ack()
    _process_csv(body, client, "saison")


@slack_app.action("process_csv_bank")
def handle_process_csv_bank(ack, body, client):
    """銀行CSVを処理"""
    ack()
    _process_csv(body, client, "bank")


def _process_csv(body, client, csv_type: str):
    """CSV処理の共通ロジック"""
    import requests

    file_id = body["actions"][0]["value"]
    channel_id = body["channel"]["id"]

    try:
        from api.services.invoice_fetcher import invoice_fetcher

        # ファイル情報を取得
        file_info = client.files_info(file=file_id)
        file_data = file_info["file"]
        file_name = file_data.get("name", "")
        download_url = file_data.get("url_private_download")

        # ファイルをダウンロード
        headers = {"Authorization": f"Bearer {os.environ.get('SLACK_BOT_TOKEN')}"}
        response = requests.get(download_url, headers=headers)

        if response.status_code != 200:
            raise Exception("ファイルのダウンロードに失敗しました")

        # CSVをパース（日本の銀行CSVはShift-JIS、SaisonはUTF-8が多い）
        # 複数のエンコーディングを試す
        csv_content = None
        for encoding in ["cp932", "utf-8", "utf-8-sig", "shift_jis"]:
            try:
                csv_content = response.content.decode(encoding)
                break
            except UnicodeDecodeError:
                continue

        if csv_content is None:
            csv_content = response.content.decode("utf-8", errors="replace")

        if csv_type == "saison":
            transactions = invoice_fetcher.parse_saison_csv(csv_content)
            type_name = "Saisonカード"
        else:
            transactions = invoice_fetcher.parse_bank_csv(csv_content)
            type_name = "銀行口座"

        if not transactions:
            client.chat_postMessage(
                channel=channel_id,
                text=f"⚠️ `{file_name}` から取引を検出できませんでした。CSVフォーマットを確認してください。"
            )
            return

        # Spreadsheetに保存 (csv_transactions シート)
        from datetime import datetime
        rows = []
        for tx in transactions:
            rows.append([
                datetime.now().isoformat(),
                csv_type,
                tx.get("date", ""),
                tx.get("vendor", ""),
                str(tx.get("amount", 0)),
                file_name
            ])

        invoice_fetcher.sheets.spreadsheets().values().append(
            spreadsheetId=invoice_fetcher.spreadsheet_id,
            range="csv_transactions!A:F",
            valueInputOption="USER_ENTERED",
            body={"values": rows}
        ).execute()

        # サマリーを表示
        total = sum(tx.get("amount", 0) for tx in transactions)
        vendor_list = "\n".join([f"• {tx['vendor']}: ¥{tx['amount']:,}" for tx in transactions[:10]])

        client.chat_postMessage(
            channel=channel_id,
            text=f"""✅ *{type_name}CSV処理完了*

• ファイル: `{file_name}`
• 取引件数: {len(transactions)}件
• 合計金額: ¥{total:,}

*取引一覧（最大10件）:*
{vendor_list}
{"..." if len(transactions) > 10 else ""}

`/accounting-reconcile 期間` で照会できます。"""
        )

    except Exception as e:
        client.chat_postMessage(
            channel=channel_id,
            text=f"❌ CSV処理エラー: {str(e)}"
        )


@slack_app.action("save_invoice_credit")
def handle_save_invoice_credit(ack, body, client):
    """クレジット請求書として保存"""
    ack()
    _save_invoice_pdf(body, client, "credit")


@slack_app.action("save_invoice_bank")
def handle_save_invoice_bank(ack, body, client):
    """銀行振込請求書として保存"""
    ack()
    _save_invoice_pdf(body, client, "bank")


@slack_app.action("save_invoice_pdf")
def handle_save_invoice_pdf(ack, body, client):
    """PDFを請求書として保存（後方互換）"""
    ack()
    _save_invoice_pdf(body, client, "credit")


def _save_invoice_pdf(body, client, invoice_type: str):
    """PDF保存の共通ロジック"""
    import requests
    from datetime import datetime

    file_id = body["actions"][0]["value"]
    channel_id = body["channel"]["id"]

    try:
        from api.services.invoice_fetcher import invoice_fetcher

        # ファイル情報を取得
        file_info = client.files_info(file=file_id)
        file_data = file_info["file"]
        file_name = file_data.get("name", "invoice.pdf")
        download_url = file_data.get("url_private_download")

        # ファイルをダウンロード
        headers = {"Authorization": f"Bearer {os.environ.get('SLACK_BOT_TOKEN')}"}
        response = requests.get(download_url, headers=headers)

        if response.status_code != 200:
            raise Exception("ファイルのダウンロードに失敗しました")

        # 期間を計算
        now = datetime.now()
        period_code = f"{now.year}{now.month:02d}"

        # Google Driveに保存
        drive_result = invoice_fetcher.save_to_drive(
            response.content,
            file_name,
            period_code,
            invoice_type=invoice_type
        )

        # PDFから金額を抽出
        from api.services.invoice_fetcher import extract_amount_from_pdf
        amount = extract_amount_from_pdf(response.content)

        # Spreadsheetに記録
        type_name = "クレジット" if invoice_type == "credit" else "銀行振込"
        invoice_data = {
            "id": f"manual_{now.timestamp()}",
            "vendor": "手動アップロード",
            "amount": str(amount) if amount else "",
            "date": now.strftime("%Y-%m-%d"),
            "period": period_code,
            "source": "slack_upload",
            "drive_url": drive_result["web_view_link"],
            "type": invoice_type,
            "status": "pending"
        }
        invoice_fetcher.record_invoice(invoice_data)

        amount_text = f"\n💰 金額: ¥{amount:,}" if amount else ""
        client.chat_postMessage(
            channel=channel_id,
            text=f"✅ {type_name}請求書を保存しました！{amount_text}\n📁 <{drive_result['web_view_link']}|Google Driveで表示>"
        )

    except Exception as e:
        client.chat_postMessage(
            channel=channel_id,
            text=f"❌ 保存エラー: {str(e)}"
        )


@slack_app.action("skip_file")
def handle_skip_file(ack, body, client):
    """ファイルをスキップ"""
    ack()
    # メッセージを更新
    client.chat_update(
        channel=body["channel"]["id"],
        ts=body["message"]["ts"],
        text="ファイルをスキップしました。",
        blocks=[]
    )


@slack_app.command("/accounting-reconcile")
def handle_reconcile(ack, respond, body):
    """CSV照会を実行"""
    ack()

    text = body.get("text", "").strip()

    if not text:
        respond({
            "response_type": "ephemeral",
            "text": "❌ 期間を指定してください。\n例: `/accounting-reconcile 202602`"
        })
        return

    respond({
        "response_type": "ephemeral",
        "text": f"🔍 期間 `{text}` の照会を実行中..."
    })

    try:
        from api.services.invoice_fetcher import invoice_fetcher, parse_period

        # 期間をパース
        periods = parse_period(text)
        target_months = set()
        for p in periods:
            # YYYYMM形式に統一
            target_months.add(f"{p['year']}{p['month']:02d}")  # 202509

        # CSV取引を取得
        result = invoice_fetcher.sheets.spreadsheets().values().get(
            spreadsheetId=invoice_fetcher.spreadsheet_id,
            range="csv_transactions!A2:G1000"
        ).execute()

        rows = result.get("values", [])
        transactions = []

        for row in rows:
            if len(row) >= 5:
                tx_date = row[2] if len(row) > 2 else ""

                # 日付形式を判定してYYYYMM or YYYY-MMを抽出
                tx_month = ""
                if len(tx_date) >= 8 and tx_date[:8].isdigit():
                    # YYYYMMDD形式 (20250915)
                    tx_month = tx_date[:6]  # 202509
                elif "/" in tx_date and len(tx_date) >= 7:
                    # YYYY/MM/DD形式 (2025/09/15)
                    parts = tx_date.split("/")
                    if len(parts) >= 2:
                        tx_month = f"{parts[0]}{parts[1]}"  # 202509
                elif "-" in tx_date and len(tx_date) >= 7:
                    # YYYY-MM-DD形式 (2025-09-15)
                    parts = tx_date.split("-")
                    if len(parts) >= 2:
                        tx_month = f"{parts[0]}{parts[1]}"  # 202509

                if tx_month in target_months:
                    transactions.append({
                        "type": row[1] if len(row) > 1 else "",
                        "date": tx_date,
                        "vendor": row[3] if len(row) > 3 else "",
                        "amount": int(row[4]) if len(row) > 4 and row[4].isdigit() else 0
                    })

        if not transactions:
            respond({
                "response_type": "ephemeral",
                "text": "⚠️ CSVデータがありません。先にCSVファイルをアップロードしてください。"
            })
            return

        # 照会実行
        reconcile_result = invoice_fetcher.reconcile_csv(transactions, text)

        # 結果を表示
        matched_count = reconcile_result["matched_count"]
        missing_count = reconcile_result["missing_count"]
        total = reconcile_result["total_transactions"]

        # 除外するキーワード（手数料、利息など）
        exclude_keywords = ["手数料", "利息", "振込手数料", "入金", "税金", "給与", "給料", "年金", "保険料"]

        # ベンダー名をクリーンアップ
        def clean_vendor_name(name: str) -> str:
            # Mastercard/Visa等のプレフィックスを削除
            name = re.sub(r"^(Mastercard|MASTERCARD|Visa|VISA|JCB)\s*", "", name, flags=re.IGNORECASE)
            # TID、番号などを削除
            name = re.sub(r"\s*(TID|TIDF)[\w\d]+", "", name, flags=re.IGNORECASE)
            name = re.sub(r"\s*F[番号ԍ]F[\d]+", "", name)
            # 長すぎる場合は切り詰め
            if len(name) > 25:
                name = name[:25] + "..."
            return name.strip()

        def should_exclude(vendor: str) -> bool:
            return any(kw in vendor for kw in exclude_keywords)

        # ベンダーごとにグルーピング（銀行・クレジット別）
        from collections import defaultdict
        bank_vendors = defaultdict(lambda: {"count": 0, "total": 0})
        credit_vendors = defaultdict(lambda: {"count": 0, "total": 0})

        for tx in reconcile_result["missing"]:
            if should_exclude(tx["vendor"]):
                continue

            cleaned_name = clean_vendor_name(tx["vendor"])
            if not cleaned_name:
                continue

            tx_type = tx.get("type", "bank")

            if tx_type in ["credit", "saison"]:
                credit_vendors[cleaned_name]["count"] += 1
                credit_vendors[cleaned_name]["total"] += tx.get("amount", 0)
            else:
                bank_vendors[cleaned_name]["count"] += 1
                bank_vendors[cleaned_name]["total"] += tx.get("amount", 0)

        # 金額でソート
        bank_sorted = sorted(bank_vendors.items(), key=lambda x: x[1]["total"], reverse=True)
        credit_sorted = sorted(credit_vendors.items(), key=lambda x: x[1]["total"], reverse=True)

        bank_list = ""
        for name, data in bank_sorted[:10]:
            bank_list += f"\n• {name}: {data['count']}件 ¥{data['total']:,}"

        credit_list = ""
        for name, data in credit_sorted[:10]:
            credit_list += f"\n• {name}: {data['count']}件 ¥{data['total']:,}"

        result_text = f"""📊 *照会結果 ({text})*

• 総取引数: {total}件
• ✅ 一致: {matched_count}件
• ❌ 不足: {missing_count}件
"""

        if bank_list:
            result_text += f"\n*🏦 銀行振込（ルール登録推奨）:*{bank_list}\n"

        if credit_list:
            result_text += f"\n*💳 クレジット（ルール登録推奨）:*{credit_list}\n"

        if bank_list or credit_list:
            result_text += "\n`/accounting-add-email-rule` でルール追加するか、PDFを手動アップロードしてください。"
        else:
            result_text += "\n✨ すべての取引が照合済みです！"

        respond({
            "response_type": "ephemeral",
            "text": result_text
        })

    except Exception as e:
        respond({
            "response_type": "ephemeral",
            "text": f"❌ 照会エラー: {str(e)}"
        })


# === Flask Routes ===

@app.route("/", methods=["GET"])
@app.route("/api/slack", methods=["GET"])
def health():
    """Health check"""
    return "Accounting Bot is running!"


@app.route("/", methods=["POST"])
@app.route("/api/slack", methods=["POST"])
def slack_events():
    """Handle Slack events"""
    return slack_handler.handle(request)
