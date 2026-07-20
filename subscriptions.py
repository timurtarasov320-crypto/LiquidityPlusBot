from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional


@dataclass(frozen=True)
class SubscriptionPlan:
    code: str
    title: str
    days: int
    price_usd: float
    description: str


SUBSCRIPTION_PLANS = {
    "week": SubscriptionPlan(
        code="week",
        title="7 дней",
        days=7,
        price_usd=5.0,
        description="Доступ ко всем VIP-сигналам на 7 дней.",
    ),
    "month": SubscriptionPlan(
        code="month",
        title="1 месяц",
        days=30,
        price_usd=15.0,
        description="Доступ ко всем VIP-сигналам на 30 дней.",
    ),
    "three_months": SubscriptionPlan(
        code="three_months",
        title="3 месяца",
        days=90,
        price_usd=40.0,
        description="Доступ ко всем VIP-сигналам на 90 дней.",
    ),
    "six_months": SubscriptionPlan(
        code="six_months",
        title="6 месяцев",
        days=180,
        price_usd=75.0,
        description="Доступ ко всем VIP-сигналам на 180 дней.",
    ),
    "year": SubscriptionPlan(
        code="year",
        title="12 месяцев",
        days=365,
        price_usd=145.0,
        description="Доступ ко всем VIP-сигналам на 12 месяцев.",
    ),
}


def get_plan(plan_code: str) -> Optional[SubscriptionPlan]:
    return SUBSCRIPTION_PLANS.get(plan_code)


def get_all_plans() -> list[SubscriptionPlan]:
    return list(SUBSCRIPTION_PLANS.values())


def calculate_discounted_price(
    plan_code: str,
    discount_percent: int,
) -> Optional[float]:
    plan = get_plan(plan_code)

    if plan is None:
        return None

    safe_discount = max(0, min(int(discount_percent), 55))

    discounted_price = plan.price_usd * (
        1 - safe_discount / 100
    )

    return round(discounted_price, 2)


def calculate_subscription_end(
    plan_code: str,
    current_end: Optional[datetime] = None,
) -> Optional[datetime]:
    plan = get_plan(plan_code)

    if plan is None:
        return None

    now = datetime.now(timezone.utc)

    if current_end is not None:
        if current_end.tzinfo is None:
            current_end = current_end.replace(
                tzinfo=timezone.utc
            )

        start_date = max(now, current_end)
    else:
        start_date = now

    return start_date + timedelta(days=plan.days)


def format_price(price: float) -> str:
    if float(price).is_integer():
        return f"${int(price)}"

    return f"${price:.2f}"


def build_plan_description(
    plan_code: str,
    discount_percent: int = 0,
) -> Optional[str]:
    plan = get_plan(plan_code)

    if plan is None:
        return None

    final_price = calculate_discounted_price(
        plan_code=plan_code,
        discount_percent=discount_percent,
    )

    if final_price is None:
        return None

    original_price = format_price(plan.price_usd)
    discounted_price = format_price(final_price)

    if discount_percent > 0:
        price_text = (
            f"Обычная цена: {original_price}\n"
            f"Ваша скидка: {discount_percent}%\n"
            f"Цена со скидкой: {discounted_price}"
        )
    else:
        price_text = f"Цена: {original_price}"

    return (
        f"💎 VIP на {plan.title}\n\n"
        f"{plan.description}\n\n"
        f"{price_text}"
    )