/**
 * 原创度检测 composable
 * 处理文章原创度分析的业务逻辑
 */

import { ref, computed } from 'vue'
import { api } from '../api/client'

export function useOriginality() {
  const loading = ref(false)
  const error = ref('')
  const analysis = ref(null)

  /**
   * 执行原创度检测
   * @param {string} contentVersionId - 稿件版本 ID
   * @param {string} compareScope - 对比范围: 'all' | 'own' | 'public'
   */
  async function analyze(contentVersionId, compareScope = 'all') {
    if (!contentVersionId) {
      error.value = '请先保存稿件'
      return false
    }

    loading.value = true
    error.value = ''
    analysis.value = null

    try {
      const response = await api.post('/content/analyze-originality', {
        contentVersionId: contentVersionId,
        compareScope: compareScope,
      })
      analysis.value = response
      return true
    } catch (err) {
      error.value = err.message || '检测失败'
      return false
    } finally {
      loading.value = false
    }
  }

  /**
   * 获取原创度等级的显示标签
   */
  const levelLabel = computed(() => {
    if (!analysis.value) return ''
    const levelMap = {
      excellent: '优秀',
      good: '良好',
      fair: '中等',
      poor: '低',
    }
    return levelMap[analysis.value.level] || '未知'
  })

  /**
   * 获取原创度等级对应的样式类名
   */
  const levelClass = computed(() => {
    if (!analysis.value) return ''
    return `level-${analysis.value.level}`
  })

  return {
    loading,
    error,
    analysis,
    analyze,
    levelLabel,
    levelClass,
  }
}
