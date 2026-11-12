<script setup>
import { onMounted, ref } from 'vue'
import { getJSON, postJSON } from '../api'

const sources = ref([])
const sourceId = ref(null)
const mode = ref('incremental')
const last = ref(null)
const history = ref([])
const err = ref('')
const busy = ref(false)

async function refresh() {
  history.value = await getJSON('/api/runs')
}

async function runOnce() {
  err.value = ''
  busy.value = true
  try {
    last.value = await postJSON(`/api/sources/${sourceId.value}/runs`, { mode: mode.value })
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  sources.value = await getJSON('/api/sources')
  if (sources.value.length) sourceId.value = sources.value[0].id
  await refresh()
})
</script>
<template>
  <div class="page">
    <h1>运行台</h1>
    <p class="muted">
      选源触发增量或全量；报告中的写入 / 新开版本 / 冲突 / 跳过需与 Canonical 当前行数、
      历史版本新增与 Staging 处置同源互证。
    </p>
    <div class="panel">
      <label>
        源
        <select v-model.number="sourceId">
          <option v-for="s in sources" :key="s.id" :value="s.id">
            {{ s.id }}. {{ s.name }}<span class="muted" v-if="s.authority_group">（{{ s.authority_group }}）</span>
          </option>
        </select>
      </label>
      <label style="margin-left:1rem">
        模式
        <select v-model="mode">
          <option value="incremental">incremental</option>
          <option value="full">full</option>
        </select>
      </label>
      <button style="margin-left:1rem" :disabled="busy || !sourceId" @click="runOnce">
        {{ busy ? '运行中…' : '执行 Run' }}
      </button>
    </div>
    <p v-if="err" class="tag danger">{{ err }}</p>

    <div class="panel" v-if="last">
      <h3>最近一次报告 · Run #{{ last.id }}</h3>
      <p>
        <span class="tag" :class="last.status === 'ok' ? 'ok' : 'danger'">{{ last.status }}</span>
        模式 <span class="mono">{{ last.report?.mode }}</span>
      </p>
      <div class="tiles">
        <div class="tile"><div class="hero-num">{{ last.parsed_count }}</div><div class="muted">parsed 解析</div></div>
        <div class="tile"><div class="hero-num">{{ last.upserted_count }}</div><div class="muted">写入合计（插入+新版本+刷新）</div></div>
        <div class="tile"><div class="hero-num">{{ last.report?.inserted_count ?? '—' }}</div><div class="muted">新键插入</div></div>
        <div class="tile"><div class="hero-num">{{ last.report?.version_opened_count ?? '—' }}</div><div class="muted">新开历史版本</div></div>
        <div class="tile"><div class="hero-num">{{ last.report?.refreshed_count ?? '—' }}</div><div class="muted">同快照刷新</div></div>
        <div class="tile"><div class="hero-num">{{ last.conflict_count }}</div><div class="muted">冲突隔离</div></div>
        <div class="tile"><div class="hero-num">{{ last.skipped_count }}</div><div class="muted">跳过</div></div>
        <div class="tile"><div class="hero-num">{{ last.report?.canonical_count ?? '—' }}</div><div class="muted">当前有效主数据行</div></div>
        <div class="tile"><div class="hero-num">{{ last.report?.history_versions_added ?? '—' }}</div><div class="muted">本次历史版本新增</div></div>
      </div>
      <p class="mono muted">
        当前开放版本数={{ last.report?.current_version_count }} ·
        历史版本累计={{ last.report?.history_version_total }} ·
        watermark={{ last.report?.watermark_ts }}
      </p>
      <p>
        对账：
        <span class="tag" :class="last.report?.reconciliation?.reconciled ? 'ok' : 'danger'">
          {{ last.report?.reconciliation?.reconciled ? '全部一致' : '存在不一致' }}
        </span>
        <span class="mono muted" style="margin-left:0.5rem">
          parsed=Σstaging · 写入=接受处置 · 冲突=quarantine=冲突队列 · 当前行=开放版本数 · 历史新增=插入+新版本
        </span>
      </p>
      <ul class="mono muted" v-if="last.report?.staging_dispositions" style="font-size:0.85rem">
        <li v-for="(c, d) in last.report.staging_dispositions" :key="d">{{ d }}: {{ c }}</li>
      </ul>
    </div>

    <div class="panel">
      <h3>历史 Runs</h3>
      <table>
        <thead>
          <tr>
            <th>ID</th><th>源</th><th>模式</th><th>状态</th><th>parsed</th>
            <th>写入</th><th>新插入</th><th>新版本</th><th>刷新</th>
            <th>冲突</th><th>跳过</th><th>历史新增</th><th>对账</th><th>时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in history" :key="r.id">
            <td>{{ r.id }}</td>
            <td>{{ r.source_name }}</td>
            <td>{{ r.mode }}</td>
            <td><span class="tag" :class="r.status === 'ok' ? 'ok' : 'danger'">{{ r.status }}</span></td>
            <td>{{ r.parsed_count }}</td>
            <td>{{ r.upserted_count }}</td>
            <td>{{ r.report?.inserted_count ?? '—' }}</td>
            <td>{{ r.report?.version_opened_count ?? '—' }}</td>
            <td>{{ r.report?.refreshed_count ?? '—' }}</td>
            <td>{{ r.conflict_count }}</td>
            <td>{{ r.skipped_count }}</td>
            <td>{{ r.report?.history_versions_added ?? '—' }}</td>
            <td>
              <span class="tag" :class="r.report?.reconciliation?.reconciled ? 'ok' : 'warn'">
                {{ r.report?.reconciliation?.reconciled ? '一致' : '待查' }}
              </span>
            </td>
            <td class="mono">{{ r.created_at }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
