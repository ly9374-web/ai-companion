const WEEKDAY_NAMES = ["日", "一", "二", "三", "四", "五", "六"];

export const formatBrowserTime = (date = new Date()): string => {
  const minutes = date.getMinutes().toString().padStart(2, "0");
  return `${date.getMonth() + 1}月${date.getDate()}日，周${WEEKDAY_NAMES[date.getDay()]}，${date.getHours()}时${minutes}分`;
};
