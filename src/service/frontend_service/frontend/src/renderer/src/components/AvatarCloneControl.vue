<template>
  <div
    :class="['avatar-clone-action', { disabled: !canOpenClone }]"
    title="克隆数字人"
    @click="openCloneDialog"
  >
    <Spin v-if="uploading" wrapper-class-name="clone-spin" />
    <PictureOutlined v-else />
  </div>

  <Teleport to="body">
    <div v-if="dialogOpen" class="clone-dialog-overlay" @click.self="closeCloneDialog">
      <section class="clone-dialog-panel">
        <header class="clone-dialog-header">
          <div>
            <div class="clone-title">数字人克隆</div>
            <div class="clone-subtitle">对齐轮廓后确认照片</div>
          </div>
          <button class="icon-button" type="button" title="关闭" @click="closeCloneDialog">
            <CloseOutlined />
          </button>
        </header>

        <div class="source-tabs">
          <button
            :class="['source-tab', { active: cloneMode === 'camera' }]"
            type="button"
            @click="switchToCamera"
          >
            <CameraOutlined />
            <span>摄像头拍摄</span>
          </button>
          <button
            :class="['source-tab', { active: cloneMode === 'upload' }]"
            type="button"
            @click="openFilePicker"
          >
            <UploadOutlined />
            <span>本地图片</span>
          </button>
        </div>

        <div class="clone-preview-frame">
          <video
            v-show="!previewImageUrl && cloneMode === 'camera'"
            ref="cameraPreviewRef"
            class="camera-preview"
            autoplay
            muted
            playsinline
          />
          <div v-if="!previewImageUrl && cloneMode === 'upload'" class="upload-placeholder">
            <PictureOutlined />
            <span>选择一张图片</span>
          </div>
          <img v-if="previewImageUrl" class="captured-preview" :src="previewImageUrl" alt="avatar preview" />
          <div class="portrait-guide">
            <div class="guide-face" />
            <div class="guide-center-line" />
            <div class="guide-shoulders" />
          </div>
        </div>

        <input
          ref="fileInputRef"
          class="hidden-file"
          type="file"
          accept="image/png,image/jpeg,image/webp"
          @change="handleFileChange"
        />

        <footer class="clone-dialog-footer">
          <template v-if="previewImageUrl">
            <button class="secondary-button" type="button" @click="retakePhoto">
              <CameraOutlined />
              <span>重新拍摄</span>
            </button>
            <button class="secondary-button" type="button" @click="openFilePicker">
              <UploadOutlined />
              <span>重新选择</span>
            </button>
            <button class="primary-button" type="button" :disabled="uploading" @click="confirmUpload">
              <CheckOutlined />
              <span>使用这张照片</span>
            </button>
          </template>
          <template v-else>
            <button class="secondary-button" type="button" @click="openFilePicker">
              <UploadOutlined />
              <span>本地图片</span>
            </button>
            <button
              class="primary-button"
              type="button"
              :disabled="cloneMode !== 'camera'"
              @click="captureFromCamera"
            >
              <CameraOutlined />
              <span>拍摄照片</span>
            </button>
          </template>
        </footer>
      </section>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import {
  CameraOutlined,
  CheckOutlined,
  CloseOutlined,
  PictureOutlined,
  UploadOutlined,
} from '@ant-design/icons-vue'
import { message, Spin } from 'ant-design-vue'
import { computed, nextTick, ref } from 'vue'

import { uploadFlashHeadAvatar } from '@/apis'
import { StreamState } from '@/interface/voiceChat'
import { useMediaStore } from '@/store/media'

type CloneMode = 'camera' | 'upload'

const props = defineProps<{
  streamState: StreamState
  uploadRoute: string
}>()

const mediaStore = useMediaStore()

const dialogOpen = ref(false)
const cloneMode = ref<CloneMode>('camera')
const uploading = ref(false)
const fileInputRef = ref<HTMLInputElement>()
const cameraPreviewRef = ref<HTMLVideoElement>()
const previewImageUrl = ref('')
const selectedFile = ref<File | null>(null)

const canOpenClone = computed(
  () => props.streamState === StreamState.closed && Boolean(props.uploadRoute) && !uploading.value
)

async function openCloneDialog(): Promise<void> {
  if (!canOpenClone.value) {
    message.warning('请先停止当前对话后再克隆数字人')
    return
  }
  dialogOpen.value = true
  cloneMode.value = 'camera'
  clearPreview()
  await ensureCameraPreview()
}

function closeCloneDialog(): void {
  dialogOpen.value = false
  clearPreview()
}

async function switchToCamera(): Promise<void> {
  cloneMode.value = 'camera'
  clearPreview()
  await ensureCameraPreview()
}

function openFilePicker(): void {
  if (!canOpenClone.value) return
  cloneMode.value = 'upload'
  fileInputRef.value?.click()
}

async function handleFileChange(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  setPreviewFile(file)
  input.value = ''
}

async function ensureCameraPreview(): Promise<void> {
  if (!mediaStore.webcamAccessed) {
    await mediaStore.accessDevice()
  }
  if (mediaStore.cameraOff) {
    message.warning('请先打开摄像头')
    return
  }
  await nextTick()
  const video = cameraPreviewRef.value
  if (!video || !mediaStore.localStream) return
  if (video.srcObject !== mediaStore.localStream) {
    video.srcObject = mediaStore.localStream
  }
  await video.play().catch(() => undefined)
}

async function captureFromCamera(): Promise<void> {
  if (!canOpenClone.value) return
  await ensureCameraPreview()

  const video = cameraPreviewRef.value
  if (!video) {
    message.error('未找到摄像头画面')
    return
  }
  if (!video.videoWidth || !video.videoHeight) {
    message.error('摄像头画面尚未准备好')
    return
  }

  const canvas = document.createElement('canvas')
  canvas.width = video.videoWidth
  canvas.height = video.videoHeight
  const context = canvas.getContext('2d')
  if (!context) {
    message.error('无法截取摄像头画面')
    return
  }
  context.drawImage(video, 0, 0, canvas.width, canvas.height)
  const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/png'))
  if (!blob) {
    message.error('无法生成照片')
    return
  }

  selectedFile.value = new File([blob], 'camera-avatar.png', { type: 'image/png' })
  replacePreviewUrl(URL.createObjectURL(selectedFile.value))
}

function setPreviewFile(file: File): void {
  selectedFile.value = file
  replacePreviewUrl(URL.createObjectURL(file))
}

function replacePreviewUrl(url: string): void {
  if (previewImageUrl.value) {
    URL.revokeObjectURL(previewImageUrl.value)
  }
  previewImageUrl.value = url
}

function clearPreview(): void {
  if (previewImageUrl.value) {
    URL.revokeObjectURL(previewImageUrl.value)
  }
  previewImageUrl.value = ''
  selectedFile.value = null
}

async function retakePhoto(): Promise<void> {
  cloneMode.value = 'camera'
  clearPreview()
  await ensureCameraPreview()
}

async function confirmUpload(): Promise<void> {
  if (!selectedFile.value) {
    message.warning('请先拍摄或选择照片')
    return
  }
  await uploadAvatarFile(selectedFile.value)
}

async function uploadAvatarFile(file: File): Promise<void> {
  uploading.value = true
  try {
    const response = await uploadFlashHeadAvatar(props.uploadRoute, file)
    const payload = await response.json().catch(() => ({}))
    if (!response.ok) {
      throw new Error(payload.detail || '数字人克隆失败')
    }
    message.success('数字人形象已更新，请开始对话')
    closeCloneDialog()
  } catch (error) {
    message.error(error instanceof Error ? error.message : String(error))
  } finally {
    uploading.value = false
  }
}
</script>

<style lang="less" scoped>
.avatar-clone-action {
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
    opacity: 0.55;
    cursor: not-allowed;
  }

  &:hover:not(.disabled) {
    background: #67666a;
  }

  :global(.clone-spin .ant-spin-dot-item) {
    background-color: #fff !important;
  }
}

.clone-dialog-overlay {
  position: fixed;
  inset: 0;
  z-index: 10000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: rgba(18, 22, 34, 0.52);
  backdrop-filter: blur(10px);
}

.clone-dialog-panel {
  width: min(560px, calc(100vw - 32px));
  max-height: calc(100vh - 48px);
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 18px;
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.96);
  box-shadow: 0 24px 80px rgba(17, 24, 39, 0.3);
}

.clone-dialog-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.clone-title {
  color: #161922;
  font-size: 20px;
  font-weight: 650;
}

.clone-subtitle {
  margin-top: 4px;
  color: #667085;
  font-size: 13px;
}

.icon-button {
  width: 36px;
  height: 36px;
  border: 0;
  border-radius: 8px;
  color: #344054;
  background: #f2f4f7;
  cursor: pointer;
}

.source-tabs {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}

.source-tab,
.secondary-button,
.primary-button {
  border: 0;
  border-radius: 10px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  cursor: pointer;
}

.source-tab {
  height: 42px;
  color: #344054;
  background: #f2f4f7;
  font-size: 14px;

  &.active {
    color: #fff;
    background: #635bff;
  }
}

.clone-preview-frame {
  position: relative;
  width: 100%;
  aspect-ratio: 4 / 3;
  overflow: hidden;
  border-radius: 14px;
  background: #111827;
}

.camera-preview,
.captured-preview {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.upload-placeholder {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  color: #d0d5dd;
  font-size: 15px;
  background: #1f2937;

  .anticon {
    font-size: 42px;
  }
}

.portrait-guide {
  pointer-events: none;
  position: absolute;
  inset: 0;
  background:
    linear-gradient(rgba(0, 0, 0, 0.22), rgba(0, 0, 0, 0.22)),
    radial-gradient(ellipse 25% 32% at 50% 39%, transparent 64%, rgba(0, 0, 0, 0.22) 65%);
}

.guide-face {
  position: absolute;
  left: 50%;
  top: 18%;
  width: 34%;
  height: 46%;
  transform: translateX(-50%);
  border: 2px solid rgba(255, 255, 255, 0.88);
  border-radius: 50%;
  box-shadow: 0 0 0 999px rgba(0, 0, 0, 0.02);
}

.guide-center-line {
  position: absolute;
  left: 50%;
  top: 15%;
  width: 1px;
  height: 66%;
  background: linear-gradient(to bottom, transparent, rgba(255, 255, 255, 0.7), transparent);
}

.guide-shoulders {
  position: absolute;
  left: 50%;
  bottom: 11%;
  width: 58%;
  height: 22%;
  transform: translateX(-50%);
  border: 2px solid rgba(255, 255, 255, 0.72);
  border-top: 0;
  border-radius: 0 0 50% 50%;
}

.hidden-file {
  display: none;
}

.clone-dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  flex-wrap: wrap;
}

.secondary-button,
.primary-button {
  min-height: 42px;
  padding: 0 16px;
  font-size: 14px;
}

.secondary-button {
  color: #344054;
  background: #f2f4f7;
}

.primary-button {
  color: #fff;
  background: #635bff;

  &:disabled {
    opacity: 0.55;
    cursor: not-allowed;
  }
}

@media (max-width: 640px) {
  .clone-dialog-overlay {
    padding: 12px;
  }

  .clone-dialog-panel {
    padding: 14px;
    border-radius: 14px;
  }

  .clone-preview-frame {
    aspect-ratio: 3 / 4;
  }

  .clone-dialog-footer {
    justify-content: stretch;

    button {
      flex: 1 1 100%;
    }
  }
}
</style>
