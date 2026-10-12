// Closing or reloading the tab ends an upload in progress (the dataset stays "Upload not finished"): ask first.
export default defineNuxtPlugin(() => {
  const { uploads } = useUploads()
  window.addEventListener('beforeunload', (e) => {
    if (Object.keys(uploads.value).length) {
      e.preventDefault()
      e.returnValue = ''  // older browsers need this to show their "Leave site?" prompt
    }
  })
})
