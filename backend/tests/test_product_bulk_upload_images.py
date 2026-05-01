import base64

from routers import products


def _png_data_url() -> str:
    raw = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
    )
    return f"data:image/png;base64,{base64.b64encode(raw).decode('ascii')}"


def test_bulk_product_rows_keep_image_urls_and_embedded_images():
    embedded = _png_data_url()
    rows = [
        ["name", "price", "image_url", "image_url_2"],
        ["Starter Plan", "$29.00", "https://example.com/starter.jpg|https://example.com/starter.webp", ""],
        ["Premium Plan", "49", "", "https://example.com/premium.png"],
    ]

    items, errors = products._parse_bulk_product_rows(rows, row_images={3: [embedded]})

    assert errors == []
    assert items[0]["payload"]["images"] == [
        "https://example.com/starter.jpg",
        "https://example.com/starter.webp",
    ]
    assert items[1]["payload"]["images"] == ["https://example.com/premium.png", embedded]


def test_bulk_product_rows_report_invalid_image_input():
    rows = [
        ["name", "price", "image_url"],
        ["Broken Product", "10", "C:\\local\\image.png"],
    ]

    items, errors = products._parse_bulk_product_rows(rows)

    assert items == []
    assert errors[0]["row"] == 2
    assert "Invalid image" in errors[0]["error"]
