"""ハロートランク固定請求書の月次自動生成サービス

ハロートランクは毎月固定額(¥11,970)の請求書PDFが同一フォーマットで届く。
テンプレートPDFの発行日のみ毎月15日に変更し、翌月3日に
Google Driveのクレジットカードフォルダに格納する。

テンプレートPDF配置先: Google Drive「Accounting-bot-invoices」直下に
  「template_ハロートランク.pdf」として配置する。
ファイル名: {YYYYMMDD}_ハロートランク_11970.pdf (例: 20260215_ハロートランク_11970.pdf)
格納先: {year}年{month}月/{YYYYMM}_クレジット/
"""

import re
from datetime import date
from io import BytesIO
from typing import Optional

# 固定値
VENDOR_NAME = "ハロートランク"
FIXED_AMOUNT = 11970
INVOICE_DAY = 15

# Google Drive上のテンプレートファイル名
TEMPLATE_FILENAME = "template_ハロートランク.pdf"

# 発行日フィールドの設定
DATE_FIELD_CONFIG = {
    "date_patterns": [
        r"\d{4}年\d{1,2}月\d{1,2}日",
        r"\d{4}/\d{1,2}/\d{1,2}",
        r"\d{4}-\d{2}-\d{2}",
        r"令和\d{1,2}年\d{1,2}月\d{1,2}日",
    ],
    # pdfplumberで自動検出できなかった場合のフォールバック座標 (PDFポイント, 左下原点)
    "fallback_x": 350,
    "fallback_y": 738,
    "fallback_width": 150,
    "fallback_height": 16,
    "font_size": 10,
}


def _fetch_template_from_drive() -> bytes:
    """
    Google Drive「Accounting-bot-invoices」直下から
    テンプレートPDF (template_ハロートランク.pdf) をダウンロードする。
    """
    from api.services.invoice_fetcher import invoice_fetcher
    from googleapiclient.http import MediaIoBaseDownload

    root_folder_id = invoice_fetcher.drive_folder_id

    # テンプレートファイルを検索
    query = (
        f"name='{TEMPLATE_FILENAME}' and "
        f"'{root_folder_id}' in parents and "
        f"trashed=false"
    )
    results = invoice_fetcher.drive.files().list(
        q=query,
        spaces="drive",
        fields="files(id, name)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).execute()

    files = results.get("files", [])
    if not files:
        raise FileNotFoundError(
            f"テンプレートPDFが見つかりません。\n"
            f"Google Drive「Accounting-bot-invoices」直下に "
            f"「{TEMPLATE_FILENAME}」を配置してください。"
        )

    file_id = files[0]["id"]

    # ダウンロード
    request = invoice_fetcher.drive.files().get_media(fileId=file_id, supportsAllDrives=True)
    buf = BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue()


def _find_date_position(pdf_data: bytes) -> Optional[dict]:
    """テンプレートPDF内の発行日テキストの位置を自動検出する。"""
    import pdfplumber

    try:
        with pdfplumber.open(BytesIO(pdf_data)) as pdf:
            page = pdf.pages[0]
            words = page.extract_words()
            full_text = page.extract_text() or ""
            page_height = page.height

            for pattern in DATE_FIELD_CONFIG["date_patterns"]:
                match = re.search(pattern, full_text)
                if not match:
                    continue

                date_text = match.group(0)

                for word in words:
                    if word["text"] in date_text or date_text.startswith(word["text"]):
                        word_height = word["bottom"] - word["top"]
                        pdf_y = page_height - word["bottom"]
                        return {
                            "x": word["x0"],
                            "y": pdf_y,
                            "width": max(word["x1"] - word["x0"], 130),
                            "height": word_height + 4,
                            "text": date_text,
                        }
    except Exception as e:
        print(f"[hellotrunk] date position detection failed: {e}")

    return None


def _format_date_text(year: int, month: int, original_text: str = None) -> str:
    """元の日付フォーマットに合わせて新しい日付テキストを生成する。"""
    if original_text:
        if "年" in original_text and "月" in original_text:
            return f"{year}年{month}月{INVOICE_DAY}日"
        if "/" in original_text:
            return f"{year}/{month}/{INVOICE_DAY}"
        if "-" in original_text:
            return f"{year}-{month:02d}-{INVOICE_DAY:02d}"
    return f"{year}年{month}月{INVOICE_DAY}日"


def generate_monthly_invoice(year: int, month: int) -> bytes:
    """
    指定月のハロートランク請求書PDFを生成する。
    Google DriveからテンプレートPDFを取得し、発行日を当月15日に変更する。
    """
    from pypdf import PdfReader, PdfWriter
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont

    # Google DriveからテンプレートPDFをダウンロード
    template_data = _fetch_template_from_drive()

    # 日本語フォント登録
    try:
        pdfmetrics.registerFont(UnicodeCIDFont("HeiseiMin-W3"))
        font_name = "HeiseiMin-W3"
    except Exception:
        font_name = "Helvetica"

    # テンプレートPDFの発行日位置を検出
    date_pos = _find_date_position(template_data)

    if date_pos:
        x = date_pos["x"]
        y = date_pos["y"]
        w = date_pos["width"]
        h = date_pos["height"]
        original_text = date_pos.get("text")
    else:
        x = DATE_FIELD_CONFIG["fallback_x"]
        y = DATE_FIELD_CONFIG["fallback_y"]
        w = DATE_FIELD_CONFIG["fallback_width"]
        h = DATE_FIELD_CONFIG["fallback_height"]
        original_text = None
        print(f"[hellotrunk] using fallback coordinates: x={x}, y={y}")

    new_date_text = _format_date_text(year, month, original_text)
    font_size = DATE_FIELD_CONFIG["font_size"]

    # テンプレートPDF読み込み
    reader = PdfReader(BytesIO(template_data))
    template_page = reader.pages[0]
    page_width = float(template_page.mediabox.width)
    page_height = float(template_page.mediabox.height)

    # 日付オーバーレイ作成（白塗り + 新しい日付テキスト）
    overlay_buf = BytesIO()
    c = canvas.Canvas(overlay_buf, pagesize=(page_width, page_height))

    c.setFillColorRGB(1, 1, 1)
    c.rect(x - 2, y - 2, w + 4, h + 4, fill=True, stroke=False)

    c.setFillColorRGB(0, 0, 0)
    c.setFont(font_name, font_size)
    c.drawString(x, y + 2, new_date_text)
    c.save()

    overlay_buf.seek(0)
    overlay_reader = PdfReader(overlay_buf)

    # テンプレートとオーバーレイをマージ
    writer = PdfWriter()
    for i, page in enumerate(reader.pages):
        if i == 0:
            page.merge_page(overlay_reader.pages[0])
        writer.add_page(page)

    output_buf = BytesIO()
    writer.write(output_buf)
    return output_buf.getvalue()


def get_invoice_filename(year: int, month: int) -> str:
    """ファイル名を生成: {YYYYMMDD}_ハロートランク_11970.pdf"""
    date_str = f"{year}{month:02d}{INVOICE_DAY:02d}"
    return f"{date_str}_{VENDOR_NAME}_{FIXED_AMOUNT}.pdf"


def generate_and_upload(target_year: int = None, target_month: int = None) -> dict:
    """
    ハロートランク請求書を生成してGoogle Driveにアップロードする。

    翌月3日の実行を想定: 引数未指定時は前月を対象とする。
    例: 3月3日に実行 → 2月分の請求書(発行日: 2月15日)を生成

    Returns:
        dict: {filename, period_code, file_id, web_view_link, skipped}
    """
    from api.services.invoice_fetcher import invoice_fetcher

    # 対象月を決定（未指定時は前月）
    if target_year is None or target_month is None:
        today = date.today()
        if today.month == 1:
            target_year = today.year - 1
            target_month = 12
        else:
            target_year = today.year
            target_month = today.month - 1

    period_code = f"{target_year}{target_month:02d}"
    filename = get_invoice_filename(target_year, target_month)

    # PDF生成
    pdf_data = generate_monthly_invoice(target_year, target_month)

    # Google Driveのクレジットカードフォルダにアップロード
    drive_result = invoice_fetcher.save_to_drive(
        pdf_data, filename, period_code, invoice_type="credit"
    )

    # invoicesシートに記録
    if not drive_result.get("skipped"):
        invoice_data = {
            "id": f"hellotrunk_{period_code}",
            "vendor": VENDOR_NAME,
            "amount": str(FIXED_AMOUNT),
            "date": f"{target_year}-{target_month:02d}-{INVOICE_DAY:02d}",
            "period": period_code,
            "source": "auto_generated",
            "drive_url": drive_result.get("web_view_link", ""),
            "summary": "ハロートランク月額利用料",
            "type": "credit",
            "status": "pending",
        }
        invoice_fetcher.record_invoice(invoice_data)

    return {
        "filename": filename,
        "period_code": period_code,
        "year": target_year,
        "month": target_month,
        "file_id": drive_result.get("file_id"),
        "web_view_link": drive_result.get("web_view_link"),
        "skipped": drive_result.get("skipped", False),
    }
