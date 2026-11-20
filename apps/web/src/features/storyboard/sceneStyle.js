/** 场景级风格覆盖的纯逻辑：覆盖判定、编号读取、PATCH payload 构造。 */

/** 场景 styleConfig 为非空普通对象时视为已覆盖（防御 null / 非对象 / 空对象）。 */
export function hasSceneStyleOverride(styleConfig) {
  return Boolean(
    styleConfig &&
    typeof styleConfig === 'object' &&
    !Array.isArray(styleConfig) &&
    Object.keys(styleConfig).length > 0,
  )
}

/** 读取场景覆盖的风格编号；未覆盖或编号非法时返回空串。 */
export function sceneStyleNumber(styleConfig) {
  if (!hasSceneStyleOverride(styleConfig)) return ''
  return typeof styleConfig.style_number === 'string' ? styleConfig.style_number : ''
}

/** 构造场景 PATCH 的 styleConfig 值：设置覆盖传 { style_number }，清除传 {}（回落项目级）。 */
export function sceneStylePayload(styleNumber) {
  const number = typeof styleNumber === 'string' ? styleNumber.trim() : ''
  return number ? { style_number: number } : {}
}
