"""The bill schema sent to Sarvam Document AI Extract.

Sarvam rules: root is an object with non-empty properties, every field has a
`type` and a non-empty `description`, max nesting depth 4. The descriptions are
the main lever for accuracy -- reword them here when a field comes back wrong.
"""

BILL_SCHEMA = {
    "type": "object",
    "properties": {
        "merchant_name": {
            "type": "string",
            "description": "Name of the shop or business that issued the bill, "
            "usually the largest text at the top. Not the customer name.",
        },
        "merchant_gstin": {
            "type": "string",
            "description": "The merchant's 15-character GSTIN printed on the bill "
            "(e.g. 27AAPFU0939F1ZV). Empty if the bill has no GSTIN.",
        },
        "bill_number": {
            "type": "string",
            "description": "Invoice, bill or receipt number printed on the bill.",
        },
        "bill_date": {
            "type": "string",
            "description": "Date the bill was issued, exactly as printed "
            "(Indian bills are day first, e.g. 25/12/2024).",
        },
        "payment_mode": {
            "type": "string",
            "description": "How the customer paid: cash, upi, card or credit. "
            "Empty if not printed.",
        },
        "subtotal": {
            "type": "number",
            "description": "Total of all items before tax and discount, in INR, digits only.",
        },
        "tax_amount": {
            "type": "number",
            "description": "Total GST (CGST + SGST + IGST) charged on the bill, "
            "in INR, digits only.",
        },
        "discount": {
            "type": "number",
            "description": "Total discount given on the bill, in INR, digits only. "
            "0 if none.",
        },
        "total_amount": {
            "type": "number",
            "description": "Final grand total the customer has to pay, in INR, "
            "digits only. The last and largest amount on the bill.",
        },
        "line_items": {
            "type": "array",
            "description": "One entry per product or service row on the bill.",
            "items": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "Name of the product or service on this row.",
                    },
                    "quantity": {
                        "type": "number",
                        "description": "Quantity purchased on this row.",
                    },
                    "unit_price": {
                        "type": "number",
                        "description": "Price per single unit on this row, in INR.",
                    },
                    "amount": {
                        "type": "number",
                        "description": "Line total for this row, in INR.",
                    },
                },
            },
        },
    },
}
