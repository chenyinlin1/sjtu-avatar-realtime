<script setup lang="ts">
import { useMediaStore } from '@/store/media'
import { AudioOutlined, VideoCameraOutlined } from '@ant-design/icons-vue'
import { computed, onMounted, ref } from 'vue'

const props = withDefaults(
  defineProps<{
    autoAccess?: boolean
  }>(),
  {
    autoAccess: false,
  }
)
const mediaState = useMediaStore()
const requesting = ref(false)
const isAudioOnly = computed(() => mediaState.trackConstraints?.video === false)
const accessClick = async (): Promise<void> => {
  if (requesting.value) {
    return
  }
  requesting.value = true
  try {
    await mediaState.accessDevice()
  } finally {
    requesting.value = false
  }
}
onMounted(() => {
  if (props.autoAccess) {
    accessClick() // 自动获取权限
  }
})

const text = computed(() => {
  if (requesting.value) {
    return isAudioOnly.value ? '正在请求麦克风权限' : '正在请求摄像头和麦克风权限'
  }
  return isAudioOnly.value ? '点击允许访问麦克风' : '点击允许访问摄像头和麦克风'
})

const title = computed(() => (isAudioOnly.value ? '需要麦克风权限' : '需要摄像头和麦克风权限'))
</script>

<template>
  <div class="access-wrap" role="button" :aria-busy="requesting" @click="accessClick">
    <section class="access-card">
      <span class="icon-wrap">
        <AudioOutlined v-if="isAudioOnly" />
        <VideoCameraOutlined v-else />
      </span>
      <div class="access-eyebrow">FlashHead AI 助教</div>
      <h1>{{ title }}</h1>
      <p>{{ text }}</p>
      <span class="access-button">{{ requesting ? '正在请求' : '允许访问' }}</span>
    </section>
  </div>
</template>
<style lang="less" scoped>
.access-wrap {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 24px;
  color: #64748b;
  text-align: center;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.94), rgba(248, 250, 252, 0.9));
  cursor: pointer;
}

.access-card {
  width: min(360px, 86%);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 28px 24px;
  border: 1px solid rgba(176, 31, 46, 0.14);
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.92);
  box-shadow: 0 20px 42px rgba(15, 23, 42, 0.08);
}

.access-eyebrow {
  color: #b01f2e;
  font-size: 13px;
  font-weight: 700;
}

h1 {
  margin: 0;
  color: #1f2937;
  font-size: 20px;
  font-weight: 700;
}

p {
  margin: 0;
  font-size: 14px;
  line-height: 1.7;
}

.icon-wrap {
  width: 54px;
  height: 54px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 16px;
  color: #b01f2e;
  background: rgba(176, 31, 46, 0.12);
  font-size: 26px;
}

.access-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 38px;
  padding: 0 18px;
  margin-top: 4px;
  border-radius: 999px;
  color: #fff;
  background: #b01f2e;
  font-size: 14px;
  font-weight: 650;
}
</style>
