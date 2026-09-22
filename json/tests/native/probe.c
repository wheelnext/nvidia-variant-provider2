/* Test-only local library. Never shipped in a runtime artifact. */
#include <stdint.h>
#include <string.h>
#ifdef _WIN32
#define API __declspec(dllexport)
#else
#define API __attribute__((visibility("default")))
#endif
static int initialized;
static int device;
API uint32_t nvmlInitWithFlags(uint32_t flags) { if(flags) return 2; initialized=1; return 0; }
API uint32_t nvmlShutdown(void) { initialized=0; return 0; }
API uint32_t nvmlSystemGetDriverVersion(char *out, uint32_t size) {
    if(!initialized) return 1; if(size<6) return 7; memcpy(out,"580.1",6); return 0;
}
API uint32_t nvmlSystemGetCudaDriverVersion_v2(int32_t *out) { if(!initialized) return 1; *out=12070; return 0; }
API uint32_t nvmlSystemGetCudaDriverVersion(int32_t *out) { return nvmlSystemGetCudaDriverVersion_v2(out); }
API uint32_t nvmlDeviceGetCount_v2(uint32_t *out) { if(!initialized) return 1; *out=1; return 0; }
API uint32_t nvmlDeviceGetHandleByIndex_v2(uint32_t index, void **out) {
    if(!initialized) return 1; if(index) return 2; *out=&device; return 0;
}
API uint32_t nvmlDeviceGetCudaComputeCapability(void *handle, int32_t *major, int32_t *minor) {
    if(!initialized) return 1; if(handle!=&device) return 2; *major=8; *minor=9; return 0;
}
API uint32_t probe_open(void **out) { *out=&device; return 0; }
API uint32_t probe_read(void *handle, int64_t input, int64_t *out) {
    if(handle!=&device) return 2; *out=input+7; return 0;
}
API uint32_t probe_close(void) { return 0; }
API uint32_t probe_failure(void) { return 17; }
