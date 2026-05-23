import os
import razorpay

RAZORPAY_KEY_ID     = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")

# Prices in paise  (₹2,999 = 299900 paise)
PLANS = {
    "starter": {
        "name":    "Starter",
        "price":   299900,
        "display": "₹2,999",
        "desc":    "1 firm · 50 tasks/month",
        "clients": 1,
    },
    "pro": {
        "name":    "Pro",
        "price":   799900,
        "display": "₹7,999",
        "desc":    "5 firms · unlimited tasks",
        "clients": 5,
    },
    "agency": {
        "name":    "Agency",
        "price":   199900,
        "display": "₹1,999",   # per firm billed quarterly — adjust as needed
        "desc":    "Unlimited firms · priority support",
        "clients": 999,
    },
}


def _client():
    return razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))


def create_order(plan: str) -> dict:
    plan_data = PLANS[plan]
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
