"""Example transformation: tidy customer names and derive a size flag.

Copy this into a workspace's `transformations/` directory and adjust
`APPLIES_TO`. It is named with an `example_` prefix so a scan does not pick it
up by accident — rename it to activate it.
"""

NAME = "example_normalize"
APPLIES_TO = "orders*"
DESCRIPTION = "Title-case the customer, derive a size flag, and drop blank rows"
VERSION = "1"

LARGE_ORDER = 100.0


def transform(records, context):
    """Return the transformed records.

    Args:
        records: list of dicts, one per row of the incoming payload.
        context: carries ``entity``, the observed ``shape``, ``source``,
            ``rows``, and a ``log(*parts)`` helper (there is no ``print``).

    Returns:
        A list of dicts. The shape is re-inferred from what you return, so
        adding ``is_large`` here makes it appear in the generated model on the
        next cycle without anyone declaring it.
    """
    out = []
    dropped = 0

    for row in records:
        customer = str(row.get("customer") or "").strip()
        if not customer:
            dropped += 1
            continue

        cleaned = dict(row)
        cleaned["customer"] = customer.title()
        try:
            total = float(row.get("total") or 0)
        except (TypeError, ValueError):
            # Leave the value alone rather than inventing one: the shape layer
            # already recorded that this field did not parse.
            total = 0.0
        cleaned["is_large"] = total >= LARGE_ORDER
        out.append(cleaned)

    context["log"](f"{context['entity']}: kept {len(out)}, dropped {dropped} blank")
    return out
