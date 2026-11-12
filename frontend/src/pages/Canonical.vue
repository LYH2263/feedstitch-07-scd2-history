<script setup>
import { onMounted, ref } from 'vue'
import { getJSON } from '../api'
const rows = ref([])
const historyKey = ref('')
const versions = ref([])
const err = ref('')
const loadingHistory = ref(false)

async function load() {
  try {
    rows.value = await getJSON('/api/canonical')
  } catch (e) {
    err.value = String(e.message || e)
  }
}

async function openHistory(key) {
  loadingHistory.value = true
  err.value = ''
  try {
    if (historyKey.value === key) {
      historyKey.value = ''
      versions.value = []
      return
    }
    historyKey.value = key
    versions.value = await getJSON(`/api/canonical/${encodeURIComponent(key)}/history`)
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    loadingHistory.value = false
  }
}

onMounted(load)
</script>
<template>
  <div class="page">
    <h1>Canonical</h1>
    <p class="muted">
      当前有效主数据：每个业务键仅一行（= 历史表中 valid_to 为空的开放版本）。
      点“历史版本”查看该货号的 SCD2 版本链。
    </p>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <div class="panel">
      <p>当前有效 <strong>{{ rows.length }}</strong> 个业务键</p>
      <table>
        <thead>
          <tr>
            <th>key</th><th>title</th><th>price</th><th>category</th><th>source</th>
            <th>rev</th><th>当前版本</th><th>valid_from</th><th>event_ts</th><th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.business_key">
            <td class="mono">{{ r.business_key }}</td>
            <td>{{ r.title }}</td>
            <td class="mono">{{ r.price }}</td>
            <td>{{ r.category }}</td>
            <td>{{ r.source_id }}</td>
            <td>{{ r.source_revision }}</td>
            <td class="mono">v{{ r.version_no }}</td>
            <td class="mono">{{ r.valid_from }}</td>
            <td class="mono">{{ r.event_ts }}</td>
            <td>
              <button class="secondary" @click="openHistory(r.business_key)">
                {{ historyKey === r.business_key ? '收起历史' : '历史版本' }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="panel" v-if="historyKey">
      <h3>历史版本链 · <span class="mono">{{ historyKey }}</span></h3>
      <p class="muted" v-if="loadingHistory">加载中…</p>
      <table v-else>
        <thead>
          <tr>
            <th>版本</th><th>状态</th><th>title</th><th>price</th><th>category</th>
            <th>valid_from</th><th>valid_to</th><th>source</th><th>rev</th><th>开版本 Run</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="v in versions" :key="v.id">
            <td class="mono">v{{ v.version_no }}</td>
            <td>
              <span class="tag" :class="v.is_current ? 'ok' : 'warn'">
                {{ v.is_current ? '当前有效' : '已关闭' }}
              </span>
            </td>
            <td>{{ v.title }}</td>
            <td class="mono">{{ v.price }}</td>
            <td>{{ v.category }}</td>
            <td class="mono">{{ v.valid_from }}</td>
            <td class="mono">{{ v.valid_to || '—（开放）' }}</td>
            <td>{{ v.source_id }}</td>
            <td>{{ v.source_revision }}</td>
            <td>#{{ v.run_id }}</td>
          </tr>
        </tbody>
      </table>
      <p class="muted" style="margin-top:0.5rem">
        区间为半开 [valid_from, valid_to)：旧版本在新版本 event_ts 时刻闭合，冲突隔离行不会出现在此链中。
      </p>
    </div>
  </div>
</template>
