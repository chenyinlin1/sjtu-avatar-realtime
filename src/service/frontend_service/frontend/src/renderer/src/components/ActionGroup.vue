<template>
  <div class="action-group">
    <div v-if="hasCamera">
      <div
        v-click-outside="() => (cameraListShow = false)"
        :class="['action', { 'menu-open': cameraListShow }]"
        @click="handleCameraOff"
      >
        <Iconfont :icon="cameraOff ? CameraOff : CameraOn" />
        <div
          v-if="streamState === 'closed'"
          class="corner"
          @click.stop.prevent="() => (cameraListShow = !cameraListShow)"
        >
          <div class="corner-inner" />
        </div>
        <div
          v-show="cameraListShow && streamState === 'closed'"
          class="selectors"
          :class="{ left: isLandscape }"
        >
          <div
            v-for="device in availableVideoDevices"
            :key="device.deviceId"
            class="selector"
            @click.stop="
              () => {
                handleDeviceChange(device.deviceId)
                cameraListShow = false
              }
            "
          >
            {{ device.label }}
            <div
              v-if="selectedVideoDevice && device.deviceId === selectedVideoDevice.deviceId"
              class="active-icon"
            >
              <CheckIcon />
            </div>
          </div>
        </div>
      </div>
    </div>
    <div v-if="hasMic">
      <div
        v-click-outside="() => (micListShow = false)"
        :class="['action', { 'menu-open': micListShow }]"
        @click="handleMicMuted"
      >
        <Iconfont :icon="micMuted ? MicOff : MicOn" />
        <div
          v-if="streamState === 'closed'"
          class="corner"
          @click.stop.prevent="() => (micListShow = !micListShow)"
        >
          <div class="corner-inner" />
        </div>
        <div
          v-show="micListShow && streamState === 'closed'"
          class="selectors"
          :class="{ left: isLandscape }"
        >
          <div
            v-for="device in availableAudioDevices"
            :key="device.deviceId"
            class="selector"
            @click.stop="
              () => {
                handleDeviceChange(device.deviceId)
                micListShow = false
              }
            "
          >
            {{ device.label }}
            <div
              v-if="selectedAudioDevice && device.deviceId === selectedAudioDevice.deviceId"
              class="active-icon"
            >
              <CheckIcon />
            </div>
          </div>
        </div>
      </div>
    </div>


    <div v-if="webPersonaEnabled">
      <div
        v-click-outside="() => (personaListShow = false)"
        :class="['action', 'persona-action', { 'menu-open': personaListShow, disabled: !canChangePersona }]"
        :title="selectedPersonaLabel ? `当前角色：${selectedPersonaLabel}` : '选择角色'"
        @click="togglePersonaList"
      >
        <UserOutlined />
        <div
          v-if="streamState === 'closed'"
          class="corner"
          @click.stop.prevent="() => (personaListShow = !personaListShow)"
        >
          <div class="corner-inner" />
        </div>
        <div
          v-show="personaListShow && streamState === 'closed'"
          class="selectors persona-selectors"
          :class="{ left: isLandscape }"
        >
          <div v-if="!webPersonaItems.length" class="selector persona-empty-selector">
            暂无角色
          </div>
          <div
            v-for="persona in webPersonaItems"
            :key="persona.persona_id"
            class="selector"
            @click.stop="handlePersonaSelect(persona.persona_id)"
          >
            {{ persona.display_name || persona.persona_id }}
            <div v-if="persona.persona_id === selectedPersonaId" class="active-icon">
              <CheckIcon />
            </div>
          </div>
          <div class="selector persona-create-selector" @click.stop="openCreatePersona">
            <PlusOutlined />
            <span>新建角色</span>
          </div>
        </div>
      </div>
    </div>
    <AvatarCloneControl
      v-if="avatarCloneEnabled"
      :stream-state="streamState"
      :upload-route="avatarCloneUploadRoute"
      :persona-id="selectedPersonaId"
      :persona-label="selectedPersonaLabel"
      @updated="refreshPersonas"
    />
    <VoiceCloneControl
      v-if="voiceCloneEnabled"
      :stream-state="streamState"
      :upload-route="voiceCloneUploadRoute"
      :reset-route="voiceCloneResetRoute"
      :sample-text="voiceCloneSampleText"
      :persona-id="selectedPersonaId"
      :persona-label="selectedPersonaLabel"
      @updated="refreshPersonas"
    />
    <div
      :class="['action', 'interrupt-action', { active: canInterrupt, disabled: !canInterrupt }]"
      :title="canInterrupt ? '打断当前回复' : '当前没有可打断的回复'"
      role="button"
      aria-label="打断当前回复"
      :aria-disabled="!canInterrupt"
      @click="handleInterrupt"
    >
      <Iconfont :icon="HandStop" />
    </div>
    <div class="action" @click="handleVolumeMute">
      <Iconfont :icon="volumeMuted ? VolumeOff : VolumeOn" />
    </div>
    <div v-if="wrapperRect.width > 300">
      <div class="action" @click="handleSubtitleToggle">
        <Iconfont :icon="showChatRecords ? SubtitleOn : SubtitleOff" />
      </div>
    </div>
  </div>

  <Teleport to="body">
    <div v-if="createPersonaOpen" class="persona-dialog-overlay" @click.self="closeCreatePersona">
      <section class="persona-dialog-panel">
        <header class="persona-dialog-header">
          <div>
            <div class="persona-dialog-title">新建角色</div>
          </div>
          <button class="persona-icon-button" type="button" title="关闭" @click="closeCreatePersona">
            <CloseOutlined />
          </button>
        </header>
        <label class="persona-field">
          <span>角色名称</span>
          <input
            v-model="newPersonaName"
            type="text"
            maxlength="24"
            placeholder="例如：小明"
            @keyup.enter="submitCreatePersona"
          />
        </label>
        <footer class="persona-dialog-footer">
          <button class="persona-secondary-button" type="button" @click="closeCreatePersona">取消</button>
          <button
            class="persona-primary-button"
            type="button"
            :disabled="creatingPersona || !newPersonaName.trim()"
            @click="submitCreatePersona"
          >
            创建
          </button>
        </footer>
      </section>
    </div>
  </Teleport>
</template>
<script setup lang="ts">
import { CloseOutlined, PlusOutlined, UserOutlined } from '@ant-design/icons-vue'
import { message } from 'ant-design-vue'
import { computed, ref } from 'vue'
import { storeToRefs } from 'pinia'

import { useMediaStore } from '@/store/media'
import { useChatStore } from '@/store/chat'
import { useVideoChatStore } from '@/store/webrtc'
import { useWSVideoChatStore } from '@/store/ws'
import { useAppStore } from '@/store/app'
import { useVisionStore } from '@/store/vision'
import AvatarCloneControl from './AvatarCloneControl.vue'
import VoiceCloneControl from './VoiceCloneControl.vue'
import Iconfont, {
  CameraOff,
  CameraOn,
  CheckIcon,
  HandStop,
  MicOff,
  MicOn,
  SubtitleOff,
  SubtitleOn,
  VolumeOff,
  VolumeOn,
} from './Iconfont'

const chatStore = useChatStore()
const mediaStore = useMediaStore()
const visionStore = useVisionStore()
const appStore = useAppStore()
const videoChatStore = useVideoChatStore()
const wsChatStore = useWSVideoChatStore()

const {
  hasCamera,
  hasMic,
  cameraOff,
  micMuted,
  selectedAudioDevice,
  selectedVideoDevice,
  availableAudioDevices,
  availableVideoDevices,
} = storeToRefs(mediaStore)

const { volumeMuted, showChatRecords, replying } = storeToRefs(chatStore)
const {
  avatarCloneEnabled,
  avatarCloneUploadRoute,
  voiceCloneEnabled,
  voiceCloneUploadRoute,
  voiceCloneResetRoute,
  voiceCloneSampleText,
  webPersonaEnabled,
  webPersonaItems,
  selectedPersonaId,
  selectedPersona,
} = storeToRefs(appStore)
const streamState = computed(() =>
  appStore.chatMode === 'ws' ? wsChatStore.streamState : videoChatStore.streamState
)
const canInterrupt = computed(() => replying.value && streamState.value === 'open')

const { handleVolumeMute, handleSubtitleToggle } = chatStore
const { handleCameraOff, handleMicMuted, handleDeviceChange } = mediaStore

const { wrapperRect, isLandscape } = storeToRefs(visionStore)
const micListShow = ref(false)
const cameraListShow = ref(false)
const personaListShow = ref(false)
const createPersonaOpen = ref(false)
const newPersonaName = ref('')
const creatingPersona = ref(false)
const selectedPersonaLabel = computed(
  () => selectedPersona.value?.display_name || selectedPersonaId.value || ''
)
const canChangePersona = computed(() => streamState.value === 'closed')

function togglePersonaList(): void {
  if (!canChangePersona.value) {
    message.warning('请先停止当前对话后再切换角色')
    return
  }
  personaListShow.value = !personaListShow.value
}

function handlePersonaSelect(personaId: string): void {
  appStore.selectWebPersona(personaId)
  personaListShow.value = false
}

function openCreatePersona(): void {
  if (!canChangePersona.value) return
  personaListShow.value = false
  newPersonaName.value = ''
  createPersonaOpen.value = true
}

function closeCreatePersona(): void {
  if (creatingPersona.value) return
  createPersonaOpen.value = false
}

async function submitCreatePersona(): Promise<void> {
  if (!newPersonaName.value.trim()) return
  creatingPersona.value = true
  try {
    await appStore.createWebPersona(newPersonaName.value)
    message.success('角色已创建')
    createPersonaOpen.value = false
  } catch (error) {
    message.error(error instanceof Error ? error.message : String(error))
  } finally {
    creatingPersona.value = false
  }
}

async function refreshPersonas(): Promise<void> {
  if (!webPersonaEnabled.value) return
  await appStore.refreshWebPersonas().catch((error) => {
    message.error(error instanceof Error ? error.message : String(error))
  })
}


function handleInterrupt(): void {
  if (!canInterrupt.value) return
  if (appStore.chatMode === 'ws') {
    wsChatStore.interrupt()
  } else {
    videoChatStore.interrupt()
  }
}
</script>

<style lang="less" scoped>
.action-group {
  border-radius: 12px;
  background: rgba(88, 87, 87, 0.5);
  padding: 2px;
  backdrop-filter: blur(8px);
  z-index: 200;
  position: relative;

  .action {
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

    // 下拉展开时提升按钮层级，避免被其他元素遮挡
    &.menu-open {
      z-index: 300;
    }

    .corner {
      position: absolute;
      right: 0px;
      bottom: 0px;
      padding: 3px;

      .corner-inner {
        width: 6px;
        height: 6px;
        border-top: 3px transparent solid;
        border-left: 3px transparent solid;
        border-bottom: 3px #fff solid;
        border-right: 3px #fff solid;
      }
    }

    // &:hover {
    // 	.selectors {
    // 		display: block !important;
    // 	}
    // }
    .selectors {
      position: absolute;
      top: 0;
      left: calc(100%);
      margin-left: 3px;
      max-height: 150px;
      z-index: 400;

      &.left {
        left: 0;
        margin-left: -3px;
        transform: translateX(-100%);
      }

      border-radius: 12px;
      width: max-content;
      overflow: hidden;
      overflow: auto;

      background: rgba(90, 90, 90, 0.5);
      backdrop-filter: blur(8px);

      .selector {
        max-width: 250px;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        position: relative;
        cursor: pointer;
        height: 42px;
        line-height: 42px;
        color: #fff;
        font-size: 14px;

        &:hover {
          background: #67666a;
        }

        padding-left: 15px;
        padding-right: 50px;

        .active-icon {
          position: absolute;
          right: 10px;
          width: 40px;
          height: 40px;
          display: flex;
          align-items: center;
          justify-content: center;
          top: 0;
        }
      }
    }
  }

  .action:hover {
    background: #67666a;
  }


  .persona-action.disabled {
    cursor: not-allowed;
    opacity: 0.45;
  }

  .persona-empty-selector {
    cursor: default !important;
    opacity: 0.72;
  }

  .persona-empty-selector:hover {
    background: transparent !important;
  }

  .persona-create-selector {
    display: flex !important;
    align-items: center;
    gap: 8px;
    border-top: 1px solid rgba(255, 255, 255, 0.16);
  }

  .interrupt-action {
    opacity: 0.46;

    &.active {
      opacity: 1;
      background: rgba(232, 93, 93, 0.88);
    }

    &.disabled {
      cursor: not-allowed;
    }
  }

  .interrupt-action.active:hover {
    background: #e85d5d;
  }

  .interrupt-action.disabled:hover {
    background: transparent;
  }
}

.action-group + .action-group {
  margin-top: 10px;
}

.persona-dialog-overlay {
  position: fixed;
  inset: 0;
  z-index: 10000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  background: rgba(10, 14, 22, 0.56);
  backdrop-filter: blur(6px);
}

.persona-dialog-panel {
  width: min(360px, calc(100vw - 32px));
  padding: 16px;
  border-radius: 8px;
  background: #111827;
  color: #f8fafc;
  box-shadow: 0 24px 80px rgba(0, 0, 0, 0.34);
}

.persona-dialog-header,
.persona-dialog-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.persona-dialog-title {
  font-size: 16px;
  font-weight: 700;
}

.persona-icon-button,
.persona-secondary-button,
.persona-primary-button {
  height: 34px;
  border: 0;
  border-radius: 8px;
  cursor: pointer;
}

.persona-icon-button {
  width: 34px;
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
}

.persona-field {
  display: grid;
  gap: 8px;
  margin: 16px 0;
  font-size: 13px;
  color: #cbd5e1;
}

.persona-field input {
  width: 100%;
  height: 38px;
  border: 1px solid rgba(148, 163, 184, 0.3);
  border-radius: 8px;
  padding: 0 12px;
  background: #0f172a;
  color: #fff;
  outline: none;
}

.persona-secondary-button {
  padding: 0 14px;
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
}

.persona-primary-button {
  padding: 0 16px;
  background: #3b82f6;
  color: #fff;
}

.persona-primary-button:disabled {
  cursor: not-allowed;
  opacity: 0.48;
}
</style>
