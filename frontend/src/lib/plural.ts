// Правила русского берём у Intl, а не пишем остатками от деления: 21 — «одно»,
// 11–14 — «много», хотя кончаются на 1–4. Дроби (категория other) — как 2–4:
// «1,5 обращения».
const rules = new Intl.PluralRules("ru");

/** Форма слова после числа: forms — для 1, для 2–4 и для 5+ («обращение», «обращения», «обращений»). */
export function plural(count: number, [one, few, many]: readonly [string, string, string]): string {
  switch (rules.select(count)) {
    case "one":
      return one;
    case "many":
      return many;
    default:
      return few;
  }
}
