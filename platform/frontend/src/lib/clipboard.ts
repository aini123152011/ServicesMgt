/**
 * 复制到剪贴板：优先用异步 Clipboard API，降级到 execCommand。
 *
 * 为什么必须降级：`navigator.clipboard` 只在**安全上下文**（https 或 localhost）里存在。
 * 平台在测试网里就是 `http://<宿主IP>:18080` 这种访问方式，`navigator.clipboard` 是
 * undefined，直接调会抛 TypeError —— 之前就是这样：复制没成功，界面还弹「已复制」。
 * 老接口 `document.execCommand('copy')` 在不安全上下文里仍可用（选中一个临时 textarea 再拷）。
 *
 * @returns 是否真的写进了剪贴板。调用方据此决定弹成功还是失败提示——不能假装成功。
 */
export async function copyText(text: string): Promise<boolean> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      // 权限被拒等情况下继续尝试降级路径
    }
  }
  return copyTextLegacy(text)
}

/**
 * 降级路径：临时插入一个只读 textarea、选中、execCommand('copy') 后移除。
 *
 * 两个必须的细节：
 * - `focus({ preventScroll: true })` —— execCommand('copy') 作用于**当前焦点元素**的选区，
 *   焦点在别的输入框里时选区会失效（实测：页面上有元素抢焦点就拷不进去）；preventScroll
 *   避免把页面滚回顶部（它是 fixed 定位的隐藏节点）。
 * - `readOnly` 防止移动端弹软键盘。
 */
function copyTextLegacy(text: string): boolean {
  const area = document.createElement('textarea')
  area.value = text
  area.setAttribute('readonly', '')
  area.style.position = 'fixed'
  area.style.top = '0'
  area.style.left = '0'
  area.style.opacity = '0'
  document.body.appendChild(area)
  try {
    area.focus({ preventScroll: true })
    area.select()
    area.setSelectionRange(0, text.length)
    return document.execCommand('copy')
  } catch {
    return false
  } finally {
    document.body.removeChild(area)
  }
}
