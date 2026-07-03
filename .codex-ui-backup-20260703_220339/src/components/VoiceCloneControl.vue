<template>
  <div
    :class="['voice-clone-action', { disabled: !canOpenClone }]"
    title="克隆音色"
    role="button"
    aria-label="克隆音色"
    :aria-disabled="!canOpenClone"
    @click="openDialog"
  >
    <Spin v-if="cloning" wrapper-class-name="voice-spin" />
    <AudioOutlined v-else />
  </div>

  <Teleport to="body">
    <div v-if="dialogOpen" class="voice-dialog-overlay" @click.self="closeDialog">
      <section class="voice-dialog-panel">
        <header class="voice-dialog-header">
          <div>
            <div class="voice-title">音色克隆</div>
            <div class="voice-subtitle">读完文案后试听确认</div>
          </div>
          <button class="icon-button" type="button" title="关闭" @click="closeDialog">
            <CloseOutlined />
          </button>
        </header>

        <div class="prompt-panel">
          <div class="prompt-label">朗读文案</div>
          <p>{{ normalizedSampleText }}</p>
        </div>

        <div class="recording-panel">
          <div :class="['record-orb', { active: recording, ready: Boolean(audioUrl) }]">
            <AudioOutlined />
          </div>
          <div class="recording-state">
            <span>{{ statusText }}</span>
            <strong>{{ formattedSeconds }}</strong>
          </div>
          <div class="level-bars" aria-hidden="true">
            <span v-for="index in 18" :key="index" :style="{ '--delay': `${index * 38}ms` }" />
          </div>
        </div>

        <audio v-if="audioUrl" class="audio-preview" controls :src="audioUrl" />

        <footer class="voice-dialog-footer">
          <button
            v-if="!recording"
            class="secondary-button"
            type="button"
            :disabled="cloning"
            @click="resetDefaultVoice"
          >
            <RollbackOutlined />
            <span>恢复默认</span>
          </button>
          <button
            v-if="audioUrl && !recording"
            class="secondary-button"
            type="button"
            :disabled="cloning"
            @click="clearRecording"
          >
            <RedoOutlined />
            <span>重录</span>
          </button>
          <button
            v-if="!recording && !audioUrl"
            class="primary-button"
            type="button"
            :disabled="cloning"
            @click="startRecording"
          >
            <AudioOutlined />
            <span>开始录音</span>
          </button>
          <button v-if="recording" class="primary-button stop" type="button" @click="stopRecording">
            <PauseCircleOutlined />
            <span>结束录音</span>
          </button>
          <button
            v-if="audioUrl && !recording"
            class="primary-button"
            type="button"
            :disabled="!canSubmit"
            @click="submitVoiceClone"
          >
            <CheckOutlined />
            <span>使用这个音色</span>
          </button>
        </footer>
      </section>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import {
  AudioOutlined,
  CheckOutlined,
  CloseOutlined,
  PauseCircleOutlined,
  RedoOutlined,
  RollbackOutlined,
} from '@ant-design/icons-vue'
import { message, Spin } from 'ant-design-vue'
import { computed, onBeforeUnmount, ref } from 'vue'

import { resetVoiceClone, uploadVoiceClone } from '@/apis'
import { StreamState } from '@/interface/voiceChat'
import { useMediaStore } from '@/store/media'

const MIN_RECORD_SECONDS = 5
const DEFAULT_SAMPLE_TEXT =
  '今天我想把这段声音留给数字人使用。请听我用自然的语气读完这段话，声音保持稳定，语速不用太快。等一会儿它就会用我的音色，继续完成接下来的对话。'

const props = defineProps<{
  streamState: StreamState
  uploadRoute: string
  resetRoute: string
  sampleText: string
}>()

const mediaStore = useMediaStore()
const dialogOpen = ref(false)
const recording = ref(false)
const cloning = ref(false)
const seconds = ref(0)
const audioUrl = ref('')
const audioFile = ref<File | null>(null)

let recorder: MediaRecorder | null = null
let recordingStream: MediaStream | null = null
let chunks: BlobPart[] = []
let timer: number | undefined

const canOpenClone = computed(
  () => props.streamState === StreamState.closed && Boolean(props.uploadRoute) && !cloning.value
)
const normalizedSampleText = computed(() => props.sampleText || DEFAULT_SAMPLE_TEXT)
const canSubmit = computed(
  () => Boolean(audioFile.value) && seconds.value >= MIN_RECORD_SECONDS && !recording.value && !cloning.value
)
const formattedSeconds = computed(() => `00:${String(seconds.value).padStart(2, '0')}`)
const statusText = computed(() => {
  if (cloning.value) return '正在生成专属音色'
  if (recording.value) return seconds.value < MIN_RECORD_SECONDS ? '保持朗读' : '可以结束录音'
  if (audioUrl.value) return seconds.value < MIN_RECORD_SECONDS ? '录音偏短' : '录音已就绪'
  return '建议读 10 到 20 秒'
})

async function openDialog(): Promise<void> {
  if (!canOpenClone.value) {
    message.warning('请先停止当前对话后再克隆音色')
    return
  }
  dialogOpen.value = true
}

function closeDialog(): void {
  if (recording.value) {
    stopRecording()
  }
  dialogOpen.value = false
}

function chooseMimeType(): string {
  const candidates = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4']
  return candidates.find((type) => MediaRecorder.isTypeSupported(type)) || ''
}

async function startRecording(): Promise<void> {
  if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
    message.error('当前浏览器不支持录音')
    return
  }

  clearRecording()
  const audioConstraints: MediaTrackConstraints = {
    echoCancellation: false,
    noiseSuppression: false,
    autoGainControl: false,
  }
  if (mediaStore.selectedAudioDevice?.deviceId) {
    audioConstraints.deviceId = { exact: mediaStore.selectedAudioDevice.deviceId }
  }

  try {
    recordingStream = await navigator.mediaDevices.getUserMedia({ audio: audioConstraints })
    const mimeType = chooseMimeType()
    recorder = new MediaRecorder(recordingStream, mimeType ? { mimeType } : undefined)
    chunks = []
    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) chunks.push(event.data)
    }
    recorder.onstop = finishRecording
    recorder.start()
    recording.value = true
    seconds.value = 0
    timer = window.setInterval(() => {
      seconds.value += 1
    }, 1000)
  } catch (error) {
    cleanupStream()
    message.error(error instanceof Error ? error.message : String(error))
  }
}

function stopRecording(): void {
  if (!recorder || recorder.state === 'inactive') return
  recorder.stop()
}

function finishRecording(): void {
  recording.value = false
  if (timer !== undefined) {
    window.clearInterval(timer)
    timer = undefined
  }
  cleanupStream()

  const mimeType = recorder?.mimeType || 'audio/webm'
  recorder = null
  if (!chunks.length) {
    message.error('没有录到声音，请重试')
    return
  }

  const extension = mimeType.includes('mp4') ? 'm4a' : 'webm'
  const blob = new Blob(chunks, { type: mimeType })
  audioFile.value = new File([blob], `voice-clone.${extension}`, { type: mimeType })
  audioUrl.value = URL.createObjectURL(blob)
  chunks = []

  if (seconds.value < MIN_RECORD_SECONDS) {
    message.warning('录音时间偏短，建议重录一段更完整的声音')
  }
}

function cleanupStream(): void {
  recordingStream?.getTracks().forEach((track) => track.stop())
  recordingStream = null
}

function clearRecording(): void {
  if (audioUrl.value) {
    URL.revokeObjectURL(audioUrl.value)
  }
  audioUrl.value = ''
  audioFile.value = null
  chunks = []
  seconds.value = 0
}

async function submitVoiceClone(): Promise<void> {
  if (!audioFile.value) return
  if (seconds.value < MIN_RECORD_SECONDS) {
    message.warning('请录制至少 5 秒清晰朗读')
    return
  }

  cloning.value = true
  try {
    const response = await uploadVoiceClone(props.uploadRoute, audioFile.value)
    const payload = await response.json().catch(() => ({}))
    if (!response.ok) {
      throw new Error(payload.detail || '音色克隆失败')
    }
    message.success('音色已更新，请开始对话')
    closeDialog()
  } catch (error) {
    message.error(error instanceof Error ? error.message : String(error))
  } finally {
    cloning.value = false
  }
}

async function resetDefaultVoice(): Promise<void> {
  if (!props.resetRoute) return
  cloning.value = true
  try {
    const response = await resetVoiceClone(props.resetRoute)
    const payload = await response.json().catch(() => ({}))
    if (!response.ok) {
      throw new Error(payload.detail || '恢复默认音色失败')
    }
    clearRecording()
    message.success('已恢复默认音色')
    closeDialog()
  } catch (error) {
    message.error(error instanceof Error ? error.message : String(error))
  } finally {
    cloning.value = false
  }
}

onBeforeUnmount(() => {
  if (recording.value) stopRecording()
  cleanupStream()
  clearRecording()
})
</script>

<style lang="less" scoped>
.voice-clone-action {
  cursor: pointer;
  width: 42px;
  height: 42px;
  border-radius: 8px;
  font-size: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
  color: #fff;

  &.disabled {
    cursor: not-allowed;
    opacity: 0.42;
  }

  &:not(.disabled):hover {
    background: rgba(255, 255, 255, 0.14);
  }

  :global(.voice-spin .ant-spin-dot-item) {
    background-color: #fff;
  }
}

.voice-dialog-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  background: rgba(10, 14, 22, 0.56);
  backdrop-filter: blur(6px);
}

.voice-dialog-panel {
  width: min(520px, 100%);
  max-height: min(720px, calc(100vh - 32px));
  overflow: auto;
  border-radius: 8px;
  background: #111827;
  color: #f8fafc;
  box-shadow: 0 24px 80px rgba(0, 0, 0, 0.34);
}

.voice-dialog-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 18px 20px 14px;
  border-bottom: 1px solid rgba(148, 163, 184, 0.2);
}

.voice-title {
  font-size: 18px;
  font-weight: 700;
}

.voice-subtitle {
  margin-top: 4px;
  color: #94a3b8;
  font-size: 13px;
}

.icon-button {
  width: 34px;
  height: 34px;
  border: 0;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
  cursor: pointer;
}

.prompt-panel {
  margin: 18px 20px 0;
  padding: 14px 16px;
  border: 1px solid rgba(96, 165, 250, 0.34);
  border-radius: 8px;
  background: rgba(30, 64, 175, 0.22);

  p {
    margin: 8px 0 0;
    color: #e0f2fe;
    font-size: 16px;
    line-height: 1.8;
  }
}

.prompt-label {
  color: #93c5fd;
  font-size: 13px;
  font-weight: 700;
}

.recording-panel {
  display: grid;
  grid-template-columns: 68px 1fr;
  gap: 14px;
  align-items: center;
  margin: 18px 20px 0;
  padding: 16px;
  border-radius: 8px;
  background: rgba(15, 23, 42, 0.92);
}

.record-orb {
  width: 56px;
  height: 56px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #334155;
  color: #dbeafe;
  font-size: 26px;

  &.active {
    background: #dc2626;
    box-shadow: 0 0 0 10px rgba(220, 38, 38, 0.14);
  }

  &.ready {
    background: #059669;
    box-shadow: 0 0 0 10px rgba(5, 150, 105, 0.14);
  }
}

.recording-state {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  color: #cbd5e1;
  font-size: 14px;

  strong {
    color: #fff;
    font-variant-numeric: tabular-nums;
  }
}

.level-bars {
  grid-column: 2;
  height: 30px;
  display: flex;
  align-items: center;
  gap: 4px;

  span {
    width: 4px;
    height: 10px;
    border-radius: 999px;
    background: #38bdf8;
    opacity: 0.5;
    animation: voice-bar 900ms ease-in-out infinite;
    animation-delay: var(--delay);
  }
}

.audio-preview {
  width: calc(100% - 40px);
  margin: 18px 20px 0;
}

.voice-dialog-footer {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 10px;
  padding: 18px 20px 20px;
}

.primary-button,
.secondary-button {
  min-height: 38px;
  border: 0;
  border-radius: 8px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 0 14px;
  font-size: 14px;
  cursor: pointer;

  &:disabled {
    cursor: not-allowed;
    opacity: 0.5;
  }
}

.primary-button {
  background: #2563eb;
  color: #fff;

  &.stop {
    background: #dc2626;
  }
}

.secondary-button {
  background: rgba(255, 255, 255, 0.1);
  color: #f8fafc;
}

@keyframes voice-bar {
  0%,
  100% {
    height: 8px;
    opacity: 0.38;
  }
  50% {
    height: 28px;
    opacity: 1;
  }
}

@media (max-width: 560px) {
  .voice-dialog-overlay {
    align-items: flex-end;
    padding: 12px;
  }

  .voice-dialog-panel {
    max-height: calc(100vh - 24px);
  }

  .recording-panel {
    grid-template-columns: 56px 1fr;
  }

  .voice-dialog-footer {
    justify-content: stretch;

    button {
      flex: 1 1 140px;
    }
  }
}
</style>
