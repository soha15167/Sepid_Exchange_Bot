from pathlib import Path

p = Path(__file__).resolve().parents[1] / "handlers" / "offers.py"
text = p.read_text(encoding="utf-8")
RTL = "\u200f"
pending = f'{RTL}\U0001f4ce <b>\u0641\u06cc\u0634 \u0648\u0627\u0631\u06cc\u0632 \u06cc\u0648\u0631\u0648:</b> \u23f3 \u062f\u0631 \u0627\u0646\u062a\u0638\u0627\u0631\n'
seller_line = f'{RTL}\U0001f4ce <b>\u0641\u06cc\u0634 \u0648\u0627\u0631\u06cc\u0632 \u06cc\u0648\u0631\u0648 (\u0641\u0631\u0648\u0634\u0646\u062f\u0647):</b>'

old = f"""    items = deal_gate_seller_receipt_list(oid) if oid else []
    if not items:
        return f"{{_RTL}}{pending.split(RTL)[1].rstrip(chr(10))}\\n"
    confirmed = sum(1 for r in items if int(r.get("buyer_confirmed_at") or 0) > 0)
    lines = [
        f"{{_RTL}}{seller_line.split(RTL)[1]} <b>{{len(items)}}</b> \u0645\u0648\u0631\u062f \u2705"
    ]"""

# simpler anchor
anchor = "    items = deal_gate_seller_receipt_list(oid) if oid else []\n    if not items:"
if anchor not in text:
    raise SystemExit("anchor not found")

insert = """    items = deal_gate_seller_receipt_list(oid) if oid else []
    photo_items = [r for r in items if (r.get("type") or "") == "photo"]
    text_items = [
        r
        for r in items
        if (r.get("type") or "") == "text" and (r.get("text") or "").strip()
    ]
    if slides_mode and photo_items:
        if text_items:
            tlines = [f"{_RTL}\U0001f4ce <b>\u0641\u06cc\u0634 \u0648\u0627\u0631\u06cc\u0632 \u06cc\u0648\u0631\u0648 (\u0645\u062a\u0646):</b>"]
            for r in text_items[-2:]:
                t = (r.get("text") or "").strip()[:120]
                mark = " \u2705" if int(r.get("buyer_confirmed_at") or 0) > 0 else ""
                tlines.append(
                    f"{_RTL}  \u00b7 <code>{html_module.escape(t)}</code>{mark}"
                )
            return "\\n".join(tlines) + "\\n"
        return ""
    if not items:"""

text = text.replace(anchor, insert, 1)

# fix slide caption latin o
text = text.replace("\u06cc\u0648\u0631o", "\u06cc\u0648\u0631\u0648")

# seller toman photo and slides
text = text.replace(
    "        elif (r.get(\"type\") or \"\") == \"photo\":\n            lines.append(photo_lbl)\n    return \"\\n\".join(lines) + \"\\n\"\n\n\ndef _seller_euro_fully_confirmed_gate",
    "        elif (r.get(\"type\") or \"\") == \"photo\" and not slides_mode:\n            lines.append(photo_lbl)\n    return \"\\n\".join(lines) + \"\\n\"\n\n\ndef seller_toman_receipt_slide_caption_html(gate: dict | None) -> str:\n    return f\"{_RTL}\U0001f4ce <b>\u0641\u06cc\u0634 \u062a\u0648\u0645\u0627\u0646 \u0628\u0647 \u0641\u0631\u0648\u0634\u0646\u062f\u0647</b>\"\n\n\ndef _seller_euro_fully_confirmed_gate",
    1,
)

marker2 = "    items = deal_gate_seller_toman_admin_list(oid) if oid else []\n    if not items:"
insert2 = """    items = deal_gate_seller_toman_admin_list(oid) if oid else []
    photo_items = [r for r in items if (r.get("type") or "") == "photo"]
    text_items = [
        r
        for r in items
        if (r.get("type") or "") == "text" and (r.get("text") or "").strip()
    ]
    if slides_mode and photo_items:
        if text_items:
            tlines = [f"{_RTL}\U0001f4ce <b>\u0641\u06cc\u0634 \u062a\u0648\u0645\u0627\u0646 \u0628\u0647 \u0641\u0631\u0648\u0634\u0646\u062f\u0647 (\u0645\u062a\u0646):</b>"]
            for r in text_items[-2:]:
                t = (r.get("text") or "").strip()[:120]
                tlines.append(f"{_RTL}  \u00b7 <code>{html_module.escape(t)}</code>")
            return "\\n".join(tlines) + "\\n"
        return ""
    if not items:"""
if marker2 in text:
    text = text.replace(marker2, insert2, 1)

if "receipt_slides_mode: bool = False" not in text:
    text = text.replace(
        "    embed_receipt_photos: bool = False,\n",
        "    embed_receipt_photos: bool = False,\n    receipt_slides_mode: bool = False,\n",
    )
    text = text.replace(
        "gate, embed_photos=embed_receipt_photos\n            )",
        "gate,\n                embed_photos=embed_receipt_photos,\n                slides_mode=receipt_slides_mode,\n            )",
    )
    text = text.replace("            foot += _admin_photo_order_foot_html(embed_photo_labels)\n", "")

p.write_text(text, encoding="utf-8")
print("done")
