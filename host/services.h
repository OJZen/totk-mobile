#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

bool service_connect(const char *name, uint32_t *handle);
bool service_close(uint32_t handle);
bool service_request(uint32_t handle, void *buffer, size_t size, uint32_t *result);
const char *service_blocker(void);

// Desktop virtual amiibo reader; raw dump remains unmodified.
bool service_amiibo_load(const char *path, char *error, size_t capacity);
void service_amiibo_remove(void);
bool service_amiibo_selected(void);
bool service_amiibo_delivered(void);
