import os
import razorpay

RAZORPAY_KEY_ID     = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")

# Prices in paise  (₹2,999 = 299900 paise)
# Note: Client limits are defined in plans.py only — this file handles billing display/pricing only.
PLANS = {
    "starter": {
        "name":    "Starter",
        "price":   299900,
        "display": "₹2,999",
        "desc":    "1 firm · 50 tasks/month",
    },
    "pro": {
        "name":    "Pro",
        "price":   799900,
        "display": "₹7,999",
        "desc":    "5 firms · unlimited tasks",
    },
    "agency": {
        "name":    "Agency",
        "price":   1999900,
        "display": "₹19,999",
        "desc":    "Unlimited firms · priority support",
    },
}


def _client():
    return razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))


def create_order(plan: str) -> dict:
    plan_data = PLANS.get(plan)
    if not plan_data:
        raise ValueError(f"Unknown plan: {plan!r}")
    return _client().order.create({
        "amount":   plan_data["price"],
        "currency": "INR",
        "notes":    {"plan": plan},
    })


def verify_payment(order_id: str, payment_id: str, signature: str) -> bool:
    try:
        _client().utility.verify_payment_signature({
            "razorpay_order_id":   order_id,
            "razorpay_payment_id": payment_id,
            "razorpay_signature":  signature,
        })
        return True
    except razorpay.errors.SignatureVerificationError:
        return False
