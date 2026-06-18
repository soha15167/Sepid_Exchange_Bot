/** Step prompts — same text as handlers/euro_flow.py & exchange_flow.py */

export const PAYMENT_HINTS = {
  buy: "📥 روش‌های دریافت را انتخاب کنید:\n• چندانتخابی: IBAN / PayPal / Wise / Revolut\n• تک‌انتخابی: معاوضه Euro به Euro\n\nپس از انتخاب، «بعدی» را بزنید.",
  sell: "💳 روش‌های پرداخت را انتخاب کنید:\n• چندانتخابی: IBAN / PayPal / Wise / Revolut\n• تک‌انتخابی: معاوضه Euro به Euro\n\nپس از انتخاب، «بعدی» را بزنید.",
} as const;

export const EURO_FLOW_HINTS = {
  country: {
    title: "🌍 کشور حساب بانکی",
    body: "کشور حساب بانکی آگهی دهنده را وارد کنید:",
    example: "مثال: آلمان",
  },
  instant: {
    title: "🏦 واریز آنی",
    body: "آیا امکان واریز آنی را دارید:",
    options: [
      { value: "have", label: "دارم" },
      { value: "dont_have", label: "ندارم" },
      { value: "unknown", label: "اطلاعی ندارم" },
    ],
  },
  amount: {
    title: "💶 مقدار یورو",
    body: "لطفاً مقدار یورو مورد نظر را وارد کنید",
    example: "مثال: 1200",
  },
  rate: {
    title: "💰 نرخ (تومان)",
    body: "لطفاً نرخ مورد نظر را به تومان بصورت کامل و بدون هیچ علامت و حرف اضافه ای وارد کنید",
    example: "فقط عدد، مثال: 190000",
  },
  description: {
    title: "📝 توضیحات",
    body: "لطفاً توضیحات خود را وارد کنید (مثلاً ساعت یا شرایط انتقال):",
    example: "اگر توضیحی ندارید، بنویسید: ندارم",
  },
  preview: {
    title: "📣 پیش‌نمایش آگهی",
    body: "پس از بررسی، با «انتشار در کانال» آگهی در کانال تلگرام منتشر می‌شود.",
  },
} as const;

export const EXCHANGE_FLOW_HINTS = {
  delivery_buy: {
    title: "📥 روش دریافت یورو",
    body: "لطفاً روش دریافت یورو را انتخاب کنید:",
  },
  delivery_sell: {
    title: "📤 روش تحویل یورو",
    body: "لطفاً روش تحویل یورو را انتخاب کنید:",
  },
  instant: EURO_FLOW_HINTS.instant,
  amount: {
    title: "💶 مقدار یورو",
    body: "لطفاً مقدار یورو را وارد کنید:",
    example: "فقط عدد صحیح",
  },
  country: {
    title: "🌍 کشور حساب",
    body: "لطفاً کشور حساب بانکی آگهی دهنده (خارج از ایران) را وارد کنید:",
  },
  cityInt: {
    title: "🌆 شهر خارج",
    body: "لطفا نام شهر خارج از ایران را وارد کنید:",
  },
  cityIr: {
    title: "🏙️ شهر ایران",
    body: "لطفا نام شهر داخل ایران را وارد کنید:",
  },
  description: {
    title: "📝 توضیحات",
    body: "لطفا توضیحات خود را وارد کنید. اگر توضیحی ندارید، بنویسید: ندارم",
  },
  preview: {
    title: "📣 پیش‌نمایش معاوضه",
    body: "پس از تأیید، آگهی معاوضه در کانال منتشر می‌شود.",
  },
} as const;

export const EXCHANGE_DELIVERY_OPTIONS = {
  buy: [
    { value: "transfer" as const, label: "امکان دریافت به حساب دارم" },
    { value: "in_person" as const, label: "امکان دریافت حضوری دارم (دریافت حضوری)" },
  ],
  sell: [
    { value: "transfer" as const, label: "امکان واریز دارم" },
    { value: "in_person" as const, label: "امکان واریز ندارم (تحویل حضوری)" },
  ],
} as const;
