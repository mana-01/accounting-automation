"""Vercel serverless function entry point for Slack."""

import os
import json
import re
import traceback
from datetime import datetime
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
• `/accounting-status` - 今月の照会状況を確認
• `/accounting-add-subscription` - メール取得ルールを追加
• `/accounting-subscriptions` - ルール一覧・リマインド項目管理
• `/accounting-fetch-invoices [期間]` - メールから請求書を自動取得
• `/accounting-register-invoices <期間>` - Drive上のPDFをシートに登録
• `/accounting-invoices` - 取得済み請求書一覧
• `/accounting-reconcile [期間]` - CSV照会を実行
• `/accounting-share [期間]` - 税理士さんに請求書を共有
• `/accounting-generate-hellotrunk [期間]` - ハロートランク請求書を手動生成
• `/accounting-diagnose` - Google Drive アクセス診断

*期間指定の例:*
• `202602` - 2026年2月分のみ
• `202509~202601` - 2025年9月〜2026年1月

*使い方:*
1. `/accounting-add-subscription` でメール取得ルールを設定
2. `/accounting-fetch-invoices 202602` でメールから請求書を取得
3. CSVファイルをアップロード（Saisonまたは銀行）
4. `/accounting-reconcile 202602` で照会
5. 不足している請求書がリストアップされます
6. `/accounting-share 202602` で税理士さんに共有

*フォルダ構造:*
📁 2026年2月/
├── 📁 202602_クレジット/
└── 📁 202602_銀行振込/"""
    })


@slack_app.command("/accounting-diagnose")
def handle_diagnose(ack, respond):
    """Google Drive アクセス診断"""
    ack()
    try:
        from api.services.invoice_fetcher import get_google_credentials
        from googleapiclient.discovery import build

        lines = ["*🔍 Google Drive アクセス診断*\n"]

        # 1. サービスアカウント情報
        creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")
        if creds_json:
            creds_dict = json.loads(creds_json)
            client_email = creds_dict.get("client_email", "(不明)")
            client_id = creds_dict.get("client_id", "(不明)")
            lines.append(f"*1. サービスアカウント:*")
            lines.append(f"  `client_email`: `{client_email}`")
            lines.append(f"  `client_id`: `{client_id}`")
        else:
            lines.append("*1.* ❌ `GOOGLE_CREDENTIALS_JSON` 未設定")

        # 2. GOOGLE_DELEGATE_EMAIL
        delegate_email = os.environ.get("GOOGLE_DELEGATE_EMAIL", "")
        lines.append(f"\n*2. GOOGLE_DELEGATE_EMAIL:*")
        if delegate_email:
            lines.append(f"  ✅ `{delegate_email}`")
        else:
            lines.append(f"  ⚠️ 未設定")

        # 3. フォルダ ID
        drive_folder = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "")
        card_folder = os.environ.get("ACCOUNTANT_CARD_FOLDER_ID", "")
        bank_folder = os.environ.get("ACCOUNTANT_BANK_FOLDER_ID", "")
        lines.append(f"\n*3. フォルダ ID:*")
        lines.append(f"  `GOOGLE_DRIVE_FOLDER_ID`: `{drive_folder or '(未設定)'}`")
        lines.append(f"  `ACCOUNTANT_CARD_FOLDER_ID`: `{card_folder or '(未設定)'}`")
        lines.append(f"  `ACCOUNTANT_BANK_FOLDER_ID`: `{bank_folder or '(未設定)'}`")

        # 4. アクセステスト
        lines.append(f"\n*4. アクセステスト:*")
        scopes = ["https://www.googleapis.com/auth/drive"]
        credentials = get_google_credentials(scopes)
        service = build("drive", "v3", credentials=credentials)

        test_folders = [
            ("メインフォルダ", drive_folder),
            ("税理士カードフォルダ", card_folder),
            ("税理士銀行フォルダ", bank_folder),
        ]

        for label, folder_id in test_folders:
            if not folder_id:
                lines.append(f"  {label}: ⏭️ ID 未設定")
                continue
            try:
                result = service.files().get(
                    fileId=folder_id,
                    fields="id, name",
                    supportsAllDrives=True
                ).execute()
                name = result.get("name", "?")
                lines.append(f"  {label}: ✅ `{name}`")
            except Exception as e:
                err = str(e)
                if "404" in err:
                    lines.append(f"  {label}: ❌ 404 Not Found")
                elif "403" in err:
                    lines.append(f"  {label}: ❌ 403 権限不足")
                else:
                    lines.append(f"  {label}: ❌ `{err[:100]}`")

        # 5. ヒント
        lines.append(f"\n*💡 税理士フォルダが ❌ の場合:*")
        if delegate_email:
            lines.append(f"  → ドメイン全体の委任が未設定の可能性あり")
            lines.append(f"  → または `{client_email}` を共有ドライブのメンバーに追加")
        else:
            lines.append(f"  方法1: `{client_email}` を共有ドライブのメンバーに追加")
            lines.append(f"  方法2: `GOOGLE_DELEGATE_EMAIL` を設定してドメイン委任を利用")

        respond({
            "response_type": "ephemeral",
            "text": "\n".join(lines)
        })
    except Exception as e:
        respond({
            "response_type": "ephemeral",
            "text": f"❌ 診断中にエラー: {str(e)}\n```{traceback.format_exc()[-500:]}```"
        })


@slack_app.command("/accounting-status")
def handle_status(ack, respond):
    """状況を表示"""
    ack()
    try:
        from api.services.invoice_fetcher import invoice_fetcher
        from collections import defaultdict

        result = invoice_fetcher.sheets.spreadsheets().values().get(
            spreadsheetId=invoice_fetcher.spreadsheet_id,
            range="invoices!A2:H1000"
        ).execute()
        rows = result.get("values", [])

        rules_result = invoice_fetcher.sheets.spreadsheets().values().get(
            spreadsheetId=invoice_fetcher.spreadsheet_id,
            range="email_rules!A2:E100"
        ).execute()
        rules = rules_result.get("values", [])

        # カラム: id(0), vendor(1), amount(2), date(3), source(4), drive_url(5), status(6), created_at(7)
        matched_vendors = defaultdict(lambda: {"count": 0, "total": 0})
        pending_vendors = defaultdict(lambda: {"count": 0, "total": 0})
        matched_total = 0
        pending_total = 0

        for row in rows:
            vendor = row[1] if len(row) > 1 else "不明"
            amount = 0
            if len(row) > 2 and row[2]:
                try:
                    amount = int(str(row[2]).replace(",", "").replace("¥", ""))
                except (ValueError, TypeError):
                    pass
            status = row[6] if len(row) > 6 else "pending"

            if status == "matched":
                matched_vendors[vendor]["count"] += 1
                matched_vendors[vendor]["total"] += amount
                matched_total += 1
            else:
                pending_vendors[vendor]["count"] += 1
                pending_vendors[vendor]["total"] += amount
                pending_total += 1

        text = f"📊 *経理状況*\n\n"
        text += f"• メール取得ルール: {len(rules)}件\n"
        text += f"• 取得済み請求書: {len(rows)}件\n"
        text += f"  - ✅ 照合済み: {matched_total}件\n"
        text += f"  - 📋 未照合: {pending_total}件\n"

        if matched_vendors:
            matched_sorted = sorted(matched_vendors.items(), key=lambda x: x[1]["total"], reverse=True)
            text += f"\n*✅ 照合済み ({matched_total}件):*\n"
            for name, data in matched_sorted:
                text += f"• {name}: {data['count']}件 ¥{data['total']:,}\n"

        if pending_vendors:
            pending_sorted = sorted(pending_vendors.items(), key=lambda x: x[1]["total"], reverse=True)
            text += f"\n*📋 未照合 ({pending_total}件):*\n"
            for name, data in pending_sorted:
                text += f"• {name}: {data['count']}件 ¥{data['total']:,}\n"

        if pending_total > 0:
            text += "\n`/accounting-reconcile YYYYMM` でCSV取引との照合を実行できます。"

        respond({
            "response_type": "ephemeral",
            "text": text
        })
    except Exception as e:
        respond({
            "response_type": "ephemeral",
            "text": f"📊 *経理状況*\n\nデータ取得中にエラー: {str(e)}"
        })


@slack_app.command("/accounting-add-subscription")
def handle_add_subscription(ack, client, body, respond):
    """メール取得ルール追加モーダル（新名称）"""
    ack()
    _open_add_rule_modal(client, body, respond)


@slack_app.command("/accounting-add-email-rule")
def handle_add_email_rule(ack, client, body, respond):
    """メール取得ルール追加モーダル（旧名称 - 後方互換）"""
    ack()
    _open_add_rule_modal(client, body, respond)


# モーダル定義（モジュール読み込み時に構築してコールドスタートの影響を最小化）
_ADD_RULE_MODAL_VIEW = {
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


def _open_add_rule_modal(client, body, respond):
    """ルール追加モーダルを開く共通関数"""
    try:
        client.views_open(
            trigger_id=body["trigger_id"],
            view=_ADD_RULE_MODAL_VIEW,
        )
    except Exception as e:
        error_msg = str(e)
        if "expired_trigger_id" in error_msg or "trigger" in error_msg.lower():
            respond({
                "response_type": "ephemeral",
                "text": "サーバー起動に時間がかかりました。もう一度コマンドを実行してください。"
            })
        else:
            respond({
                "response_type": "ephemeral",
                "text": f"フォームの表示に失敗しました: {error_msg}"
            })


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
    """メールから請求書を自動取得（Gmail→Drive保存 + Gemini解析→シート登録の2段階）"""
    ack()

    text = body.get("text", "").strip()
    user_id = body.get("user_id")

    if text:
        respond({
            "response_type": "ephemeral",
            "text": f"📥 期間 `{text}` の請求書を取得中...\n完了したらお知らせします（数分かかる場合があります）。"
        })
    else:
        respond({
            "response_type": "ephemeral",
            "text": "📥 過去30日のメールから請求書を取得中...\n完了したらお知らせします（数分かかる場合があります）。"
        })

    try:
        # Step 1: モジュールインポート
        client.chat_postMessage(
            channel=user_id,
            text="🔄 [1/4] モジュールを読み込み中..."
        )

        try:
            from api.services.invoice_fetcher import invoice_fetcher
        except Exception as import_error:
            client.chat_postMessage(
                channel=user_id,
                text=f"❌ インポートエラー:\n```{traceback.format_exc()}```"
            )
            return

        # Step 2: 設定確認
        if not invoice_fetcher.gmail_users:
            client.chat_postMessage(
                channel=user_id,
                text="❌ エラー: Gmailアカウントが設定されていません。GMAIL_USER_EMAILS環境変数を確認してください。"
            )
            return

        client.chat_postMessage(
            channel=user_id,
            text=f"🔄 [2/4] 設定確認OK\n📧 検索対象: {', '.join(invoice_fetcher.gmail_users)}"
        )

        # Step 3: ルール取得
        try:
            rules = invoice_fetcher.get_email_rules()
            client.chat_postMessage(
                channel=user_id,
                text=f"🔄 [3/4] メール取得ルール: {len(rules)}件取得"
            )
        except Exception as rule_error:
            client.chat_postMessage(
                channel=user_id,
                text=f"❌ ルール取得エラー:\n```{traceback.format_exc()}```"
            )
            return

        # Step 4: Gmail検索 → Drive保存 → 自動的にGemini解析・シート登録
        client.chat_postMessage(
            channel=user_id,
            text=f"🔄 [4/4] メール検索・PDF保存・登録中...\n（ルール{len(rules)}件 x アカウント{len(invoice_fetcher.gmail_users)}件）"
        )

        if text:
            results = invoice_fetcher.fetch_invoices_by_period(text)
        else:
            results = invoice_fetcher.fetch_invoices(days_back=30)

        # 結果サマリー構築
        periods_info = ""
        for p in results.get("periods_created", [])[:5]:
            periods_info += f"\n• {p['period']}"

        summary_parts = [
            f"• 処理したメール: {results['processed']}件",
            f"• Drive保存: {results['saved']}件",
            f"• シート登録: {results['registered']}件",
        ]
        if results.get("skipped", 0) > 0:
            summary_parts.append(f"• スキップ（重複）: {results['skipped']}件")

        invoice_list = ""
        for inv in results.get("invoices", [])[:5]:
            if inv.get("status") == "link_found":
                continue
            invoice_list += f"\n• {inv.get('vendor', '不明')} ({inv.get('date', '')}) ¥{inv.get('amount', '?')}"

        all_errors = results.get("errors", []) + results.get("register_errors", [])
        if all_errors:
            error_text = "\n".join(all_errors[:5])
            summary_parts.append(f"\n⚠️ *エラー ({len(all_errors)}件):*\n{error_text}")

        summary = "\n".join(summary_parts)

        if results["saved"] == 0 and not all_errors:
            client.chat_postMessage(
                channel=user_id,
                text=f"✅ *完了* — 新しい請求書PDFはありませんでした。{periods_info}"
            )
        else:
            client.chat_postMessage(
                channel=user_id,
                text=f"✅ *請求書取得完了*{periods_info}\n{summary}{invoice_list}\n\n💡 CSVをアップロードして `/accounting-reconcile` で照合できます。"
            )

    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        print(f"[fetch-invoices] UNHANDLED ERROR: {type(e).__name__}: {e}")
        print(error_detail)
        try:
            client.chat_postMessage(
                channel=user_id,
                text=f"❌ 予期しないエラー:\n`{type(e).__name__}: {e}`\n\n```{error_detail[:1500]}```"
            )
        except Exception:
            pass


@slack_app.command("/accounting-register-invoices")
def handle_register_invoices(ack, respond, body, client):
    """Drive上の既存PDFをinvoicesシートに登録"""
    ack()

    text = body.get("text", "").strip()
    user_id = body.get("user_id")

    if not text:
        respond({
            "response_type": "ephemeral",
            "text": "❌ 期間を指定してください。\n例: `/accounting-register-invoices 202509` または `202509~202602`"
        })
        return

    respond({
        "response_type": "ephemeral",
        "text": f"🔍 期間 `{text}` のDriveフォルダを走査中...\n未登録のPDFをシートに登録します。"
    })

    try:
        from api.services.invoice_fetcher import invoice_fetcher

        results = invoice_fetcher.register_from_drive(text)

        invoice_list = ""
        for inv in results.get("invoices", [])[:10]:
            invoice_list += f"\n• {inv.get('vendor', '不明')} ({inv.get('date', '')}) ¥{inv.get('amount', '?')}"

        summary = f"• スキャン: {results['scanned']}件\n• 新規登録: {results['registered']}件\n• スキップ（登録済み）: {results['skipped']}件"

        if results["errors"]:
            error_text = "\n".join(results["errors"][:5])
            summary += f"\n\n⚠️ *エラー ({len(results['errors'])}件):*\n{error_text}"

        client.chat_postMessage(
            channel=user_id,
            text=f"✅ *Drive登録完了 ({text})*\n{summary}{invoice_list}"
        )

    except Exception as e:
        client.chat_postMessage(
            channel=user_id,
            text=f"❌ 登録エラー: {str(e)}"
        )


@slack_app.command("/accounting-invoices")
def handle_invoices(ack, respond, body, client):
    """請求書一覧"""
    ack()
    user_id = body.get("user_id")
    respond({
        "response_type": "ephemeral",
        "text": "📄 請求書一覧を取得中..."
    })

    try:
        from api.services.invoice_fetcher import invoice_fetcher

        result = invoice_fetcher.sheets.spreadsheets().values().get(
            spreadsheetId=invoice_fetcher.spreadsheet_id,
            range="invoices!A2:H100"
        ).execute()

        rows = result.get("values", [])

        if not rows:
            client.chat_postMessage(
                channel=user_id,
                text="📄 *請求書一覧*\n\nまだ請求書がありません。`/accounting-fetch-invoices` で取得してください。"
            )
            return

        # カラム: id(0), vendor(1), amount(2), date(3), source(4), drive_url(5), status(6), created_at(7)
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

        client.chat_postMessage(
            channel=user_id,
            text=f"📄 *請求書一覧（最新10件）*\n{invoice_list}"
        )

    except Exception as e:
        client.chat_postMessage(
            channel=user_id,
            text=f"❌ エラー: {str(e)}"
        )


@slack_app.command("/accounting-email-rules")
def handle_email_rules(ack, respond, body, client):
    """メール取得ルール一覧（旧名称 - 後方互換）"""
    _list_subscriptions(ack, respond, body, client)


@slack_app.command("/accounting-subscriptions")
def handle_subscriptions(ack, respond, body, client):
    """メール取得ルール一覧"""
    _list_subscriptions(ack, respond, body, client)


def _list_subscriptions(ack, respond, body, client):
    """ルール一覧 + リマインド項目の共通処理"""
    ack()
    user_id = body.get("user_id")
    respond({
        "response_type": "ephemeral",
        "text": "📧 一覧を取得中..."
    })

    try:
        from api.services.invoice_fetcher import invoice_fetcher

        # --- 取得ルール一覧を email_rules シートから読み込み ---
        all_rules = _read_email_rules()
        email_items = [r for r in all_rules if r["category"] == "email"]
        manual_items = [r for r in all_rules if r["category"] == "manual"]
        scan_items = [r for r in all_rules if r["category"] == "scan"]

        blocks = [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "📋 *取得ルール一覧*"}
            },
            {"type": "divider"}
        ]

        if not all_rules:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": "_ルールがありません。`/accounting-add-subscription` で追加してください。_"}
            })

        # 📧 メールで自動取得
        if email_items:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": "*📧 メールで自動取得*"}
            })
            for item in email_items:
                type_text = "PDF添付" if item["fetch_type"] == "attachment" else "リンク"
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*{item['name']}*\n送信者: `{item['sender_email']}`\n件名: `{item['subject_pattern']}`\nタイプ: {type_text}"
                    },
                    "accessory": {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "削除"},
                        "style": "danger",
                        "action_id": "delete_email_rule",
                        "value": str(item["row_num"])
                    }
                })

        # ✋ 手動確認
        if manual_items:
            blocks.append({"type": "divider"})
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": "*✋ 手動確認*"}
            })
            for item in manual_items:
                label = f"*{item['name']}*"
                if item["url"]:
                    label = f"<{item['url']}|*{item['name']}*>"
                if item["notes"]:
                    label += f"（{item['notes']}）"
                blocks.append({
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": label},
                    "accessory": {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "削除"},
                        "style": "danger",
                        "action_id": "delete_email_rule",
                        "value": str(item["row_num"])
                    }
                })

        # 📄 固定スキャン
        if scan_items:
            blocks.append({"type": "divider"})
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": "*📄 固定スキャン*"}
            })
            for item in scan_items:
                label = f"*{item['name']}*"
                if item["notes"]:
                    label += f"（{item['notes']}）"
                blocks.append({
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": label},
                    "accessory": {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "削除"},
                        "style": "danger",
                        "action_id": "delete_email_rule",
                        "value": str(item["row_num"])
                    }
                })

        blocks.append({"type": "divider"})
        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "➕ 項目を追加"},
                    "style": "primary",
                    "action_id": "open_add_email_rule_modal"
                }
            ]
        })

        client.chat_postMessage(
            channel=user_id,
            blocks=blocks,
            text="📋 取得ルール一覧"
        )

    except Exception as e:
        client.chat_postMessage(
            channel=user_id,
            text=f"❌ エラー: {str(e)}"
        )


@slack_app.action("delete_email_rule")
def handle_delete_email_rule(ack, body, client):
    """取得ルールを削除（is_active=falseに設定）"""
    ack()

    row_num = body["actions"][0]["value"]
    user_id = body["user"]["id"]

    try:
        sheets, spreadsheet_id = _get_email_rules_sheets()

        # is_activeをfalseに更新（列L = 12番目）
        sheets.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"email_rules!L{row_num}",
            valueInputOption="RAW",
            body={"values": [["false"]]}
        ).execute()

        client.chat_postMessage(
            channel=user_id,
            text="✅ ルールを削除しました。`/accounting-subscriptions` で確認してください。"
        )

    except Exception as e:
        client.chat_postMessage(
            channel=user_id,
            text=f"❌ 削除エラー: {str(e)}"
        )


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
    import time as _time

    file_id = body["actions"][0]["value"]
    channel_id = body["channel"]["id"]

    try:
        from api.services.invoice_fetcher import invoice_fetcher

        # ファイル情報を取得
        file_info = client.files_info(file=file_id)
        file_data = file_info["file"]
        file_name = file_data.get("name", "")
        download_url = file_data.get("url_private_download")

        # ファイルをダウンロード（タイムアウト+リトライ付き）
        headers = {"Authorization": f"Bearer {os.environ.get('SLACK_BOT_TOKEN')}"}
        response = None
        last_error = None
        for attempt in range(3):
            try:
                response = requests.get(download_url, headers=headers, timeout=30)
                if response.status_code == 200:
                    break
            except requests.exceptions.Timeout as e:
                last_error = e
                print(f"[process_csv] download timeout (attempt {attempt+1}/3)")
                _time.sleep(2 ** attempt)
            except requests.exceptions.RequestException as e:
                last_error = e
                print(f"[process_csv] download error (attempt {attempt+1}/3): {e}")
                _time.sleep(2 ** attempt)

        if response is None or response.status_code != 200:
            status = response.status_code if response else "no response"
            raise Exception(f"ファイルのダウンロードに失敗しました: {last_error or f'status={status}'}")

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
        # カラム: uploaded_at(A), csv_type(B), date(C), vendor(D), amount(E), file_name(F), status(G)
        from datetime import datetime
        rows = []
        for tx in transactions:
            rows.append([
                datetime.now().isoformat(),
                csv_type,
                tx.get("date", ""),
                tx.get("vendor", ""),
                str(tx.get("amount", 0)),
                file_name,
                "pending"
            ])

        invoice_fetcher.sheets.spreadsheets().values().append(
            spreadsheetId=invoice_fetcher.spreadsheet_id,
            range="csv_transactions!A:G",
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
    """PDF保存の共通ロジック（Gemini解析→命名ルールでDrive保存→シート登録）"""
    import requests
    from datetime import datetime
    import time as _time

    file_id = body["actions"][0]["value"]
    channel_id = body["channel"]["id"]

    try:
        from api.services.invoice_fetcher import (
            invoice_fetcher, extract_invoice_data_with_gemini, format_invoice_filename
        )

        # ファイル情報を取得
        file_info = client.files_info(file=file_id)
        file_data = file_info["file"]
        original_filename = file_data.get("name", "")
        download_url = file_data.get("url_private_download")

        # ファイルをダウンロード（タイムアウト+リトライ付き）
        headers = {"Authorization": f"Bearer {os.environ.get('SLACK_BOT_TOKEN')}"}
        response = None
        last_error = None
        for attempt in range(3):
            try:
                response = requests.get(download_url, headers=headers, timeout=30)
                if response.status_code == 200:
                    break
            except requests.exceptions.Timeout as e:
                last_error = e
                print(f"[save_invoice_pdf] download timeout (attempt {attempt+1}/3)")
                _time.sleep(2 ** attempt)
            except requests.exceptions.RequestException as e:
                last_error = e
                print(f"[save_invoice_pdf] download error (attempt {attempt+1}/3): {e}")
                _time.sleep(2 ** attempt)

        if response is None or response.status_code != 200:
            status = response.status_code if response else "no response"
            raise Exception(f"ファイルのダウンロードに失敗しました: {last_error or f'status={status}'}")

        # Gemini APIで請求書データを抽出
        pdf_info = extract_invoice_data_with_gemini(response.content)
        amount = pdf_info.get("amount")
        # ベンダー名: Gemini結果 → 元のSlackファイル名（拡張子除去）→ フォールバック
        original_name_stem = re.sub(r'\.[^.]+$', '', original_filename).strip() if original_filename else ""
        vendor = pdf_info.get("vendor") or original_name_stem or "手動アップロード"
        now = datetime.now()
        inv_date = pdf_info.get("date") or now.strftime("%Y-%m-%d")

        # 期間を計算
        period_code = f"{now.year}{now.month:02d}"

        # 命名ルールでファイル名を生成してGoogle Driveに保存
        filename = format_invoice_filename(inv_date, vendor, amount)
        drive_result = invoice_fetcher.save_to_drive(
            response.content,
            filename,
            period_code,
            invoice_type=invoice_type
        )

        # Spreadsheetに記録
        type_name = "クレジット" if invoice_type == "credit" else "銀行振込"
        invoice_data = {
            "id": f"manual_{now.timestamp()}",
            "vendor": vendor,
            "amount": str(amount) if amount else "",
            "date": inv_date,
            "period": period_code,
            "source": "slack_upload",
            "drive_url": drive_result["web_view_link"],
            "summary": pdf_info.get("summary") or "",
            "type": invoice_type,
            "status": "pending"
        }
        registered = invoice_fetcher.record_invoice(invoice_data)

        amount_text = f"\n💰 金額: ¥{amount:,}" if amount else ""
        vendor_text = f"\n🏢 請求元: {vendor}" if vendor != "手動アップロード" else ""
        summary_text = f"\n📝 内容: {pdf_info.get('summary')}" if pdf_info.get("summary") else ""
        if registered:
            client.chat_postMessage(
                channel=channel_id,
                text=f"✅ {type_name}請求書を保存しました！\n📄 ファイル名: `{filename}`{vendor_text}{amount_text}{summary_text}\n📁 <{drive_result['web_view_link']}|Google Driveで表示>"
            )
        else:
            client.chat_postMessage(
                channel=channel_id,
                text=f"⚠️ {type_name}請求書をDriveに保存しましたが、シートには登録済みのためスキップしました。\n📄 ファイル名: `{filename}`{vendor_text}{amount_text}\n📁 <{drive_result['web_view_link']}|Google Driveで表示>"
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


@slack_app.command("/accounting-generate-hellotrunk")
def handle_generate_hellotrunk(ack, respond, body, client):
    """ハロートランク請求書を手動生成"""
    ack()

    text = body.get("text", "").strip()
    user_id = body.get("user_id")

    respond({
        "response_type": "ephemeral",
        "text": "ハロートランク請求書を生成中..."
    })

    try:
        from api.services.hellotrunk_invoice import generate_and_upload

        target_year = None
        target_month = None

        # 期間指定がある場合 (例: 202602)
        if text and len(text) == 6:
            try:
                target_year = int(text[:4])
                target_month = int(text[4:6])
            except ValueError:
                client.chat_postMessage(
                    channel=user_id,
                    text="期間の形式が正しくありません。例: `202602`"
                )
                return

        result = generate_and_upload(target_year, target_month)
        period = f"{result['year']}年{result['month']}月"

        if result.get("skipped"):
            client.chat_postMessage(
                channel=user_id,
                text=f"ハロートランク請求書 ({period}) は既に存在します。スキップしました。"
            )
        else:
            client.chat_postMessage(
                channel=user_id,
                text=(
                    f"*ハロートランク請求書を生成しました*\n"
                    f"対象月: {period}\n"
                    f"ファイル名: `{result['filename']}`\n"
                    f"<{result.get('web_view_link', '')}|Google Driveで表示>"
                )
            )

    except FileNotFoundError as e:
        client.chat_postMessage(
            channel=user_id,
            text=f"テンプレートエラー: {e}"
        )
    except Exception as e:
        client.chat_postMessage(
            channel=user_id,
            text=f"ハロートランク請求書生成エラー: {e}"
        )


@slack_app.command("/accounting-reconcile")
def handle_reconcile(ack, respond, body, client):
    """CSV照会を実行"""
    ack()

    text = body.get("text", "").strip()
    user_id = body.get("user_id")

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

        # CSV取引を取得（カラム: uploaded_at(A), csv_type(B), date(C), vendor(D), amount(E), file_name(F), status(G)）
        result = invoice_fetcher.sheets.spreadsheets().values().get(
            spreadsheetId=invoice_fetcher.spreadsheet_id,
            range="csv_transactions!A2:G1000"
        ).execute()

        rows = result.get("values", [])
        transactions = []
        already_matched_count = 0

        for row_idx, row in enumerate(rows):
            if len(row) >= 5:
                # status列（G列）を確認: matched はスキップ
                tx_status = row[6] if len(row) > 6 else ""
                if tx_status == "matched":
                    # 期間内かどうかもチェックしてカウント
                    tx_date = row[2] if len(row) > 2 else ""
                    tx_month = ""
                    if len(tx_date) >= 8 and tx_date[:8].isdigit():
                        tx_month = tx_date[:6]
                    elif "/" in tx_date:
                        parts = tx_date.split("/")
                        if len(parts) >= 2:
                            tx_month = f"{parts[0]}{parts[1].zfill(2)}"
                    elif "-" in tx_date:
                        parts = tx_date.split("-")
                        if len(parts) >= 2:
                            tx_month = f"{parts[0]}{parts[1].zfill(2)}"
                    if tx_month in target_months:
                        already_matched_count += 1
                    continue

                tx_date = row[2] if len(row) > 2 else ""

                # 日付形式を判定してYYYYMM or YYYY-MMを抽出
                tx_month = ""
                if len(tx_date) >= 8 and tx_date[:8].isdigit():
                    # YYYYMMDD形式 (20250915)
                    tx_month = tx_date[:6]  # 202509
                elif "/" in tx_date:
                    # YYYY/MM/DD形式 (2025/09/15, 2025/9/15 等)
                    parts = tx_date.split("/")
                    if len(parts) >= 2:
                        tx_month = f"{parts[0]}{parts[1].zfill(2)}"  # 202509
                elif "-" in tx_date:
                    # YYYY-MM-DD形式 (2025-09-15)
                    parts = tx_date.split("-")
                    if len(parts) >= 2:
                        tx_month = f"{parts[0]}{parts[1].zfill(2)}"  # 202509

                if tx_month in target_months:
                    # 金額パース（カンマ・通貨記号を除去）
                    tx_amount_str = row[4] if len(row) > 4 else "0"
                    try:
                        tx_amount = int(str(tx_amount_str).replace(",", "").replace("¥", "").replace("円", "").strip())
                    except (ValueError, TypeError):
                        tx_amount = 0

                    transactions.append({
                        "type": row[1] if len(row) > 1 else "",
                        "date": tx_date,
                        "vendor": row[3] if len(row) > 3 else "",
                        "amount": tx_amount,
                        "_sheet_row": row_idx + 2  # スプレッドシートの実際の行番号（ヘッダー行=1）
                    })

        if not transactions and already_matched_count == 0:
            client.chat_postMessage(
                channel=user_id,
                text="⚠️ CSVデータがありません。先にCSVファイルをアップロードしてください。"
            )
            return

        if not transactions and already_matched_count > 0:
            client.chat_postMessage(
                channel=user_id,
                text=f"✨ 期間 `{text}` のCSV取引はすべて照合済みです（{already_matched_count}件）。"
            )
            return

        # 照会実行
        reconcile_result = invoice_fetcher.reconcile_csv(transactions, text)

        # マッチしたCSV取引・請求書の両方のstatusを「matched」に更新
        matched_items = reconcile_result.get("matched", [])
        duplicate_items = reconcile_result.get("duplicate_matched", [])
        all_matched = matched_items + duplicate_items

        if all_matched:
            batch_updates = []
            for m in all_matched:
                # csv_transactions のstatus更新
                tx_sheet_row = m["transaction"].get("_sheet_row")
                if tx_sheet_row:
                    batch_updates.append({
                        "range": f"csv_transactions!G{tx_sheet_row}",
                        "values": [["matched"]]
                    })
                # invoices のstatus更新（フォールバック用、新規マッチのみ）
                if m in matched_items:
                    inv_sheet_row = m["invoice"].get("_sheet_row")
                    if inv_sheet_row:
                        batch_updates.append({
                            "range": f"invoices!G{inv_sheet_row}",
                            "values": [["matched"]]
                        })
            if batch_updates:
                invoice_fetcher.sheets.spreadsheets().values().batchUpdate(
                    spreadsheetId=invoice_fetcher.spreadsheet_id,
                    body={
                        "valueInputOption": "RAW",
                        "data": batch_updates
                    }
                ).execute()

        # 結果を表示
        matched_count = reconcile_result["matched_count"]
        duplicate_count = reconcile_result.get("duplicate_count", 0)
        missing_count = reconcile_result["missing_count"]
        total = reconcile_result["total_transactions"]

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

        # マッチしたベンダーのサマリー作成
        from collections import defaultdict
        matched_vendors = defaultdict(lambda: {"count": 0, "total": 0})
        for m in matched_items:
            tx = m["transaction"]
            vendor_name = clean_vendor_name(tx.get("vendor", "不明")) or "不明"
            matched_vendors[vendor_name]["count"] += 1
            matched_vendors[vendor_name]["total"] += tx.get("amount", 0)

        # 不足取引をベンダーごとにグルーピング（銀行・クレジット別）
        bank_vendors = defaultdict(lambda: {"count": 0, "total": 0})
        credit_vendors = defaultdict(lambda: {"count": 0, "total": 0})
        fee_count = 0

        for tx in reconcile_result["missing"]:
            if "手数料" in tx["vendor"]:
                fee_count += 1
                continue

            cleaned_name = clean_vendor_name(tx["vendor"]) or tx["vendor"]
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
        for name, data in bank_sorted:
            bank_list += f"\n• {name}: {data['count']}件 ¥{data['total']:,}"

        credit_list = ""
        for name, data in credit_sorted:
            credit_list += f"\n• {name}: {data['count']}件 ¥{data['total']:,}"

        result_text = f"""📊 *照会結果 ({text})*

• 総取引数: {total}件
• ✅ 今回一致: {matched_count}件
• ❌ 不足: {missing_count - fee_count}件
"""
        if fee_count > 0:
            result_text += f"• 🔇 手数料（税金等除外）: {fee_count}件\n"
        if duplicate_count > 0:
            result_text += f"• 🔄 重複取引（一致済み）: {duplicate_count}件\n"
        if already_matched_count > 0:
            result_text += f"• ⏭️ 照合済みスキップ: {already_matched_count}件\n"

        # 一致した取引のサマリー
        if matched_vendors:
            matched_sorted = sorted(matched_vendors.items(), key=lambda x: x[1]["total"], reverse=True)
            matched_list = ""
            for name, data in matched_sorted:
                matched_list += f"\n• {name}: {data['count']}件 ¥{data['total']:,}"
            result_text += f"\n*✅ 一致した取引:*{matched_list}\n"
            result_text += "\n_※ スプレッドシートの csv_transactions シートで status = matched を確認できます_\n"

        if bank_list:
            result_text += f"\n*🏦 銀行振込:*{bank_list}\n"

        if credit_list:
            result_text += f"\n*💳 クレジット:*{credit_list}\n"

        if not (bank_list or credit_list) and not matched_vendors:
            result_text += "\n✨ すべての取引が照合済みです！"

        client.chat_postMessage(
            channel=user_id,
            text=result_text
        )

    except Exception as e:
        client.chat_postMessage(
            channel=user_id,
            text=f"❌ 照会エラー: {str(e)}"
        )


@slack_app.command("/accounting-share")
def handle_share(ack, respond, body, client):
    """税理士さんにファイルを共有"""
    ack()

    text = body.get("text", "").strip()
    user_id = body.get("user_id")

    if not text:
        # 今月をデフォルトに
        now = datetime.now()
        text = f"{now.year}{now.month:02d}"

    respond({
        "response_type": "ephemeral",
        "text": f"📤 期間 `{text}` のファイルを税理士さんの共有フォルダにコピーしています..."
    })

    try:
        from api.services.invoice_fetcher import invoice_fetcher, parse_period

        accountant_card_folder_id = os.environ.get("ACCOUNTANT_CARD_FOLDER_ID", "")
        accountant_bank_folder_id = os.environ.get("ACCOUNTANT_BANK_FOLDER_ID", "")

        if not accountant_card_folder_id and not accountant_bank_folder_id:
            client.chat_postMessage(
                channel=user_id,
                text="❌ 税理士共有フォルダが設定されていません。環境変数 `ACCOUNTANT_CARD_FOLDER_ID` / `ACCOUNTANT_BANK_FOLDER_ID` を確認してください。"
            )
            return

        # 期間をYYYY年M月形式に変換
        periods = parse_period(text)
        period_label = f"{periods[0]['year']}年{periods[0]['month']}月" if periods else text

        # 請求書を取得
        invoices = invoice_fetcher.get_invoices_for_period(text)

        if not invoices:
            client.chat_postMessage(
                channel=user_id,
                text=f"⚠️ {period_label} の請求書がありません。先に `/accounting-fetch-invoices {text}` で取得してください。"
            )
            return

        # email_rulesからサブスク情報を取得して支払い方法を判定
        # invoicesのtypeフィールドまたはDriveフォルダ構造から判別
        card_file_ids = []
        bank_file_ids = []

        for inv in invoices:
            drive_url = inv.get("drive_url", "")
            if not drive_url:
                continue

            # Drive URLからファイルIDを抽出
            file_id = _extract_drive_file_id(drive_url)
            if not file_id:
                continue

            inv_type = inv.get("type", "credit")
            if inv_type == "bank":
                bank_file_ids.append(file_id)
            else:
                card_file_ids.append(file_id)

        # 共有フォルダにコピー
        result = _copy_to_accountant_folders(
            invoice_fetcher.drive,
            period_label,
            card_file_ids,
            bank_file_ids,
            accountant_card_folder_id,
            accountant_bank_folder_id
        )

        # 結果メッセージ
        result_lines = [f"✅ *{period_label}* のファイルを税理士さんの共有フォルダにコピーしました！\n"]

        if result["card_count"] > 0:
            result_lines.append(
                f"• 💳 クレジットカード: {result['card_count']}件 "
                f"<{result['card_folder_url']}|フォルダを開く>"
            )

        if result["bank_count"] > 0:
            result_lines.append(
                f"• 🏦 銀行: {result['bank_count']}件 "
                f"<{result['bank_folder_url']}|フォルダを開く>"
            )

        if result["card_count"] == 0 and result["bank_count"] == 0:
            result_lines.append("⚠️ コピー対象のファイルがありませんでした。")

        client.chat_postMessage(
            channel=user_id,
            text="\n".join(result_lines)
        )

    except Exception as e:
        client.chat_postMessage(
            channel=user_id,
            text=f"❌ 税理士共有でエラーが発生しました: {str(e)}"
        )


@slack_app.action("share_with_accountant")
def handle_share_with_accountant(ack, body, client, action):
    """税理士さんに請求書ファイルを共有（ボタンアクション）"""
    ack()

    channel_id = body["channel"]["id"]
    period_code = action["value"]

    # ボタンを処理中メッセージに更新
    client.chat_update(
        channel=channel_id,
        ts=body["message"]["ts"],
        text=f"📤 {period_code} のファイルを税理士さんの共有フォルダにコピーしています...",
        blocks=[
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"📤 *{period_code}* のファイルを税理士さんの共有フォルダにコピーしています..."
                }
            }
        ]
    )

    try:
        from api.services.invoice_fetcher import invoice_fetcher, parse_period

        accountant_card_folder_id = os.environ.get("ACCOUNTANT_CARD_FOLDER_ID", "")
        accountant_bank_folder_id = os.environ.get("ACCOUNTANT_BANK_FOLDER_ID", "")

        # 期間ラベル
        periods = parse_period(period_code)
        period_label = f"{periods[0]['year']}年{periods[0]['month']}月" if periods else period_code

        # 請求書を取得
        invoices = invoice_fetcher.get_invoices_for_period(period_code)

        card_file_ids = []
        bank_file_ids = []

        for inv in invoices:
            drive_url = inv.get("drive_url", "")
            if not drive_url:
                continue

            file_id = _extract_drive_file_id(drive_url)
            if not file_id:
                continue

            inv_type = inv.get("type", "credit")
            if inv_type == "bank":
                bank_file_ids.append(file_id)
            else:
                card_file_ids.append(file_id)

        result = _copy_to_accountant_folders(
            invoice_fetcher.drive,
            period_label,
            card_file_ids,
            bank_file_ids,
            accountant_card_folder_id,
            accountant_bank_folder_id
        )

        result_lines = [f"✅ *{period_label}* のファイルを税理士さんの共有フォルダにコピーしました！\n"]

        if result["card_count"] > 0:
            result_lines.append(
                f"• 💳 クレジットカード: {result['card_count']}件 "
                f"<{result['card_folder_url']}|フォルダを開く>"
            )

        if result["bank_count"] > 0:
            result_lines.append(
                f"• 🏦 銀行: {result['bank_count']}件 "
                f"<{result['bank_folder_url']}|フォルダを開く>"
            )

        if result["card_count"] == 0 and result["bank_count"] == 0:
            result_lines.append("⚠️ コピー対象のファイルがありませんでした。")

        client.chat_update(
            channel=channel_id,
            ts=body["message"]["ts"],
            text="\n".join(result_lines),
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "\n".join(result_lines)
                    }
                }
            ]
        )

    except Exception as e:
        client.chat_update(
            channel=channel_id,
            ts=body["message"]["ts"],
            text=f"❌ 税理士共有でエラーが発生しました: {str(e)}",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"❌ 税理士共有でエラーが発生しました: {str(e)}"
                    }
                }
            ]
        )


def _extract_drive_file_id(drive_url: str) -> str:
    """Google Drive URLからファイルIDを抽出"""
    # https://drive.google.com/file/d/FILE_ID/view
    match = re.search(r'/d/([a-zA-Z0-9_-]+)', drive_url)
    if match:
        return match.group(1)
    # https://drive.google.com/open?id=FILE_ID
    match = re.search(r'[?&]id=([a-zA-Z0-9_-]+)', drive_url)
    if match:
        return match.group(1)
    return ""


def _copy_to_accountant_folders(
    drive_service,
    period_label: str,
    card_file_ids: list,
    bank_file_ids: list,
    accountant_card_folder_id: str,
    accountant_bank_folder_id: str
) -> dict:
    """税理士共有フォルダに月別ファイルをコピー"""
    result = {
        "card_folder_url": "",
        "bank_folder_url": "",
        "card_count": 0,
        "bank_count": 0,
    }

    def get_or_create_subfolder(parent_id: str, name: str) -> str:
        query = (
            f"name='{name}' and "
            f"'{parent_id}' in parents and "
            f"mimeType='application/vnd.google-apps.folder' and "
            f"trashed=false"
        )
        results = drive_service.files().list(
            q=query, fields="files(id, name)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()
        files = results.get("files", [])
        if files:
            return files[0]["id"]

        folder_metadata = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id]
        }
        folder = drive_service.files().create(
            body=folder_metadata, fields="id",
            supportsAllDrives=True
        ).execute()
        return folder["id"]

    # クレジットカードファイルをコピー
    if card_file_ids and accountant_card_folder_id:
        card_subfolder_id = get_or_create_subfolder(accountant_card_folder_id, period_label)
        for file_id in card_file_ids:
            try:
                drive_service.files().copy(
                    fileId=file_id,
                    body={"parents": [card_subfolder_id]},
                    fields="id",
                    supportsAllDrives=True
                ).execute()
                result["card_count"] += 1
            except Exception as e:
                print(f"Failed to copy card file {file_id}: {e}")
        result["card_folder_url"] = f"https://drive.google.com/drive/folders/{card_subfolder_id}"

    # 銀行ファイルをコピー
    if bank_file_ids and accountant_bank_folder_id:
        bank_subfolder_id = get_or_create_subfolder(accountant_bank_folder_id, period_label)
        for file_id in bank_file_ids:
            try:
                drive_service.files().copy(
                    fileId=file_id,
                    body={"parents": [bank_subfolder_id]},
                    fields="id",
                    supportsAllDrives=True
                ).execute()
                result["bank_count"] += 1
            except Exception as e:
                print(f"Failed to copy bank file {file_id}: {e}")
        result["bank_folder_url"] = f"https://drive.google.com/drive/folders/{bank_subfolder_id}"

    return result


# === Flask Routes ===

@app.route("/api/cron/hellotrunk", methods=["GET"])
def cron_hellotrunk():
    """毎月1日にVercel Cronから呼ばれるハロートランク請求書自動生成エンドポイント（前月分）"""
    try:
        from api.services.hellotrunk_invoice import generate_and_upload

        result = generate_and_upload()
        period = f"{result['year']}年{result['month']}月"

        if result.get("skipped"):
            return json.dumps({"status": "skipped", "period": period}, ensure_ascii=False), 200

        # Slack通知
        try:
            notification_channel = os.environ.get("SLACK_NOTIFICATION_CHANNEL", "#accounting")
            slack_client = WebClient(token=os.environ.get("SLACK_BOT_TOKEN"))
            slack_client.chat_postMessage(
                channel=notification_channel,
                text=(
                    f"*ハロートランク請求書を自動生成しました*\n"
                    f"対象月: {period}\n"
                    f"ファイル名: `{result['filename']}`\n"
                    f"<{result.get('web_view_link', '')}|Google Driveで表示>"
                ),
            )
        except Exception as slack_err:
            print(f"[cron/hellotrunk] Slack notification failed: {slack_err}")

        return json.dumps({
            "status": "generated",
            "period": period,
            "filename": result["filename"],
        }, ensure_ascii=False), 200

    except Exception as e:
        print(f"[cron/hellotrunk] Error: {e}")
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False), 500


@app.route("/api/cron/fetch-invoices", methods=["GET"])
def cron_fetch_invoices():
    """毎月1日にVercel Cronから呼ばれるメール請求書自動取得エンドポイント（前月分）"""
    try:
        from datetime import date
        from api.services.invoice_fetcher import invoice_fetcher

        # 前月の期間コードを算出
        today = date.today()
        if today.month == 1:
            target_year = today.year - 1
            target_month = 12
        else:
            target_year = today.year
            target_month = today.month - 1
        period_code = f"{target_year}{target_month:02d}"
        period = f"{target_year}年{target_month}月"

        results = invoice_fetcher.fetch_invoices_by_period(period_code)

        saved = results.get("saved", 0)
        registered = results.get("registered", 0)
        skipped = results.get("skipped", 0)
        errors = results.get("errors", []) + results.get("register_errors", [])

        # Slack通知
        try:
            notification_channel = os.environ.get("SLACK_NOTIFICATION_CHANNEL", "#accounting")
            slack_client = WebClient(token=os.environ.get("SLACK_BOT_TOKEN"))

            if saved > 0 or errors:
                summary_parts = [
                    f"*メール請求書の自動取得が完了しました*",
                    f"対象月: {period}",
                    f"• Drive保存: {saved}件",
                    f"• シート登録: {registered}件",
                ]
                if skipped > 0:
                    summary_parts.append(f"• スキップ（重複）: {skipped}件")
                if errors:
                    summary_parts.append(f"• エラー: {len(errors)}件")

                slack_client.chat_postMessage(
                    channel=notification_channel,
                    text="\n".join(summary_parts),
                )
        except Exception as slack_err:
            print(f"[cron/fetch-invoices] Slack notification failed: {slack_err}")

        return json.dumps({
            "status": "completed",
            "period": period,
            "saved": saved,
            "registered": registered,
            "skipped": skipped,
            "errors": len(errors),
        }, ensure_ascii=False), 200

    except Exception as e:
        print(f"[cron/fetch-invoices] Error: {e}")
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False), 500


# === Email Rules Management (メール自動取得 / 手動確認 / スキャン) ===


def _get_email_rules_sheets():
    """email_rules シートのAPIアクセスを取得"""
    from api.services.invoice_fetcher import invoice_fetcher
    return invoice_fetcher.sheets, invoice_fetcher.spreadsheet_id


def _read_email_rules():
    """email_rules シートからアクティブな項目を読み取る"""
    sheets, spreadsheet_id = _get_email_rules_sheets()
    result = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range="email_rules!A2:M100"
    ).execute()
    rows = result.get("values", [])
    items = []
    for i, row in enumerate(rows):
        if len(row) < 3:
            continue
        # 列L (index 11) = is_active
        is_active = row[11].lower() == "true" if len(row) > 11 and row[11] else True
        if not is_active:
            continue
        items.append({
            "row_num": i + 2,
            "id": row[0] if len(row) > 0 else "",
            "name": row[1] if len(row) > 1 else "",
            "category": row[2] if len(row) > 2 else "email",
            "sender_email": row[3] if len(row) > 3 else "",
            "subject_pattern": row[4] if len(row) > 4 else "",
            "fetch_type": row[5] if len(row) > 5 else "attachment",
            "url": row[6] if len(row) > 6 else "",
            "notes": row[7] if len(row) > 7 else "",
        })
    return items


def _build_add_email_rule_modal_view(selected_category="email"):
    """カテゴリに応じた取得ルール追加モーダルを構築"""
    category_block = {
        "type": "input",
        "block_id": "category_block",
        "dispatch_action": True,
        "label": {"type": "plain_text", "text": "種別"},
        "element": {
            "type": "static_select",
            "action_id": "rule_category_select",
            "initial_option": {
                "email": {"text": {"type": "plain_text", "text": "メールで自動取得"}, "value": "email"},
                "manual": {"text": {"type": "plain_text", "text": "手動確認"}, "value": "manual"},
                "scan": {"text": {"type": "plain_text", "text": "スキャン"}, "value": "scan"},
            }[selected_category],
            "options": [
                {"text": {"type": "plain_text", "text": "メールで自動取得"}, "value": "email"},
                {"text": {"type": "plain_text", "text": "手動確認"}, "value": "manual"},
                {"text": {"type": "plain_text", "text": "スキャン"}, "value": "scan"},
            ]
        }
    }

    name_block = {
        "type": "input",
        "block_id": "name_block",
        "label": {"type": "plain_text", "text": "名前"},
        "element": {
            "type": "plain_text_input",
            "action_id": "name_input",
            "placeholder": {"type": "plain_text", "text": "例: AWS"}
        }
    }

    blocks = [category_block, name_block]

    if selected_category == "email":
        blocks.extend([
            {
                "type": "input",
                "block_id": "sender_email_block",
                "label": {"type": "plain_text", "text": "送信元メールアドレス"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "sender_email_input",
                    "placeholder": {"type": "plain_text", "text": "例: billing@example.com"}
                }
            },
            {
                "type": "input",
                "block_id": "subject_block",
                "label": {"type": "plain_text", "text": "件名パターン"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "subject_input",
                    "placeholder": {"type": "plain_text", "text": "例: 請求書"}
                }
            },
            {
                "type": "input",
                "block_id": "fetch_type_block",
                "label": {"type": "plain_text", "text": "タイプ"},
                "element": {
                    "type": "static_select",
                    "action_id": "fetch_type_select",
                    "options": [
                        {"text": {"type": "plain_text", "text": "添付PDF"}, "value": "attachment"},
                        {"text": {"type": "plain_text", "text": "リンク"}, "value": "link"},
                    ]
                }
            },
        ])
    elif selected_category == "manual":
        blocks.extend([
            {
                "type": "input",
                "block_id": "url_block",
                "label": {"type": "plain_text", "text": "確認先URL"},
                "optional": True,
                "element": {
                    "type": "url_text_input",
                    "action_id": "url_input",
                    "placeholder": {"type": "plain_text", "text": "例: https://portal.office.com/"}
                }
            },
            {
                "type": "input",
                "block_id": "notes_block",
                "label": {"type": "plain_text", "text": "備考"},
                "optional": True,
                "element": {
                    "type": "plain_text_input",
                    "action_id": "notes_input",
                    "placeholder": {"type": "plain_text", "text": "例: ログイン後マイページから取得"}
                }
            },
        ])
    elif selected_category == "scan":
        blocks.append({
            "type": "input",
            "block_id": "notes_block",
            "label": {"type": "plain_text", "text": "備考"},
            "optional": True,
            "element": {
                "type": "plain_text_input",
                "action_id": "notes_input",
                "placeholder": {"type": "plain_text", "text": "例: 毎月届く紙の領収書"}
            }
        })

    return {
        "type": "modal",
        "callback_id": "add_email_rule_modal",
        "title": {"type": "plain_text", "text": "取得ルール追加"},
        "submit": {"type": "plain_text", "text": "追加"},
        "close": {"type": "plain_text", "text": "キャンセル"},
        "blocks": blocks
    }


@slack_app.action("open_add_email_rule_modal")
def handle_open_add_email_rule_modal(ack, body, client):
    """取得ルール追加モーダルを開く"""
    ack()
    try:
        client.views_open(
            trigger_id=body["trigger_id"],
            view=_build_add_email_rule_modal_view("email"),
        )
    except Exception as e:
        print(f"[email_rules] Failed to open modal: {e}")


@slack_app.action("rule_category_select")
def handle_rule_category_select(ack, body, client):
    """種別変更時にモーダルを動的に更新"""
    ack()
    selected_category = body["actions"][0]["selected_option"]["value"]
    view_id = body["view"]["id"]
    client.views_update(
        view_id=view_id,
        view=_build_add_email_rule_modal_view(selected_category)
    )


@slack_app.view("add_email_rule_modal")
def handle_add_email_rule_submission(ack, body, client, view):
    """取得ルール追加の処理"""
    ack()

    try:
        values = view["state"]["values"]
        name = values["name_block"]["name_input"]["value"]
        category = values["category_block"]["rule_category_select"]["selected_option"]["value"]

        # カテゴリ別フィールドの取得
        sender_email = ""
        subject_pattern = ""
        fetch_type = "attachment"
        url = ""
        notes = ""

        if category == "email":
            sender_email = values["sender_email_block"]["sender_email_input"]["value"]
            subject_pattern = values["subject_block"]["subject_input"]["value"]
            fetch_type = values["fetch_type_block"]["fetch_type_select"]["selected_option"]["value"]
        elif category == "manual":
            url = values.get("url_block", {}).get("url_input", {}).get("value") or ""
            notes = values.get("notes_block", {}).get("notes_input", {}).get("value") or ""
        elif category == "scan":
            notes = values.get("notes_block", {}).get("notes_input", {}).get("value") or ""

        import uuid
        item_id = str(uuid.uuid4())
        now = datetime.now().isoformat()

        sheets, spreadsheet_id = _get_email_rules_sheets()
        # 列順: id, name, category, sender_email, subject_pattern, fetch_type, url, notes, link_selector, file_naming, subscription_id, is_active, created_at
        sheets.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range="email_rules!A:M",
            valueInputOption="USER_ENTERED",
            body={"values": [[item_id, name, category, sender_email, subject_pattern, fetch_type, url, notes, "", "rename", "", "true", now]]}
        ).execute()

        user_id = body["user"]["id"]
        category_labels = {
            "email": "📧 メールで自動取得",
            "manual": "✋ 手動確認",
            "scan": "📄 スキャン",
        }
        client.chat_postMessage(
            channel=user_id,
            text=f"✅ 取得ルールを追加しました: *{name}*（{category_labels.get(category, category)}）\n`/accounting-subscriptions` で確認できます。"
        )

    except Exception as e:
        user_id = body["user"]["id"]
        client.chat_postMessage(
            channel=user_id,
            text=f"❌ 追加に失敗しました: {str(e)}"
        )


@app.route("/", methods=["GET"])
@app.route("/api/slack", methods=["GET"])
def health():
    """Health check + diagnostics"""
    bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
    signing_secret = os.environ.get("SLACK_SIGNING_SECRET", "")
    diag = {
        "status": "running",
        "env": {
            "SLACK_BOT_TOKEN": "set" if bot_token else "MISSING",
            "SLACK_SIGNING_SECRET": "set" if signing_secret else "MISSING",
            "GOOGLE_CREDENTIALS": "set" if os.environ.get("GOOGLE_CREDENTIALS") else "MISSING",
            "GEMINI_API_KEY": "set" if os.environ.get("GEMINI_API_KEY") else "MISSING",
            "SPREADSHEET_ID": "set" if os.environ.get("SPREADSHEET_ID") else "MISSING",
        },
        "commands_registered": [
            "/accounting-help",
            "/accounting-status",
            "/accounting-add-subscription",
            "/accounting-add-email-rule",
            "/accounting-subscriptions",
            "/accounting-email-rules",
            "/accounting-fetch-invoices",
            "/accounting-register-invoices",
            "/accounting-invoices",
            "/accounting-reconcile",
            "/accounting-share",
            "/accounting-generate-hellotrunk",
        ]
    }
    return json.dumps(diag, indent=2, ensure_ascii=False), 200, {"Content-Type": "application/json"}


@app.route("/", methods=["POST"])
@app.route("/api/slack", methods=["POST"])
def slack_events():
    """Handle Slack events"""
    try:
        return slack_handler.handle(request)
    except Exception as e:
        print(f"[slack] ERROR: {type(e).__name__}: {e}")
        traceback.print_exc()
        return Response("Internal Server Error", status=500)
