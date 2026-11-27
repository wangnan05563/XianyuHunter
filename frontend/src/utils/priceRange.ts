/**
 * 任务价格区间文案（列表 / 卡片 / 详情共用一份，避免三处各写一套导致口径不一致）
 *
 * 口径：
 * - `null` / `undefined` 表示该端不限 → 文案「不限」
 * - `0` 是合法价格（「白送/0 元」），不能像旧实现那样用 `if (min)` 判空而误显示成「不限」
 * - 分隔符统一 `~`：旧详情页用 `-`、列表用 `~`，同一份数据在两处读起来像两个字段
 */
export function formatPriceRange(minPrice?: number | null, maxPrice?: number | null): string {
  const min = typeof minPrice === 'number' ? `¥${minPrice}` : '不限'
  const max = typeof maxPrice === 'number' ? `¥${maxPrice}` : '不限'
  return `${min} ~ ${max}`
}
