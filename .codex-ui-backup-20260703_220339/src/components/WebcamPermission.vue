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
</script>

<template>
  <div class="access-wrap" @click="accessClick">
    <span class="icon-wrap">
      <AudioOutlined v-if="isAudioOnly" />
      <VideoCameraOutlined v-else />
    </span>
    {{ text }}
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
}

.icon-wrap {
  width: 30px;
  font-size: 40px;
}
</style>
