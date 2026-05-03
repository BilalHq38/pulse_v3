"use client";
// Inspired by react-hot-toast library
import * as React from "react"

const TOAST_LIMIT = 1
const TOAST_REMOVE_DELAY = 1000000
const TOAST_DEDUPE_WINDOW_MS = 6000
const TYPE_TO_VARIANT = {
  success: "success",
  warning: "warning",
  info: "info",
  error: "destructive",
  destructive: "destructive",
  default: "default",
}

let count = 0

function genId() {
  count = (count + 1) % Number.MAX_SAFE_INTEGER
  return count.toString();
}

const toastTimeouts = new Map()

const addToRemoveQueue = (toastId) => {
  if (toastTimeouts.has(toastId)) {
    return
  }

  const timeout = setTimeout(() => {
    toastTimeouts.delete(toastId)
    dispatch({
      type: "REMOVE_TOAST",
      toastId: toastId,
    })
  }, TOAST_REMOVE_DELAY)

  toastTimeouts.set(toastId, timeout)
}

export const reducer = (state, action) => {
  switch (action.type) {
    case "ADD_TOAST":
      return {
        ...state,
        toasts: [action.toast, ...state.toasts].slice(0, TOAST_LIMIT),
      };

    case "UPDATE_TOAST":
      return {
        ...state,
        toasts: state.toasts.map((t) =>
          t.id === action.toast.id ? { ...t, ...action.toast } : t),
      };

    case "DISMISS_TOAST": {
      const { toastId } = action

      // ! Side effects ! - This could be extracted into a dismissToast() action,
      // but I'll keep it here for simplicity
      if (toastId) {
        addToRemoveQueue(toastId)
      } else {
        state.toasts.forEach((toast) => {
          addToRemoveQueue(toast.id)
        })
      }

      return {
        ...state,
        toasts: state.toasts.map((t) =>
          t.id === toastId || toastId === undefined
            ? {
                ...t,
                open: false,
              }
            : t),
      };
    }
    case "REMOVE_TOAST":
      if (action.toastId === undefined) {
        return {
          ...state,
          toasts: [],
        }
      }
      return {
        ...state,
        toasts: state.toasts.filter((t) => t.id !== action.toastId),
      };
    default:
      return state;
  }
}

const listeners = []

let memoryState = { toasts: [] }
const recentToastSignatures = new Map()

function toastSignature(props) {
  return [
    props.variant || "default",
    String(props.title || "").trim().toLowerCase(),
    String(props.description || "").trim().toLowerCase(),
  ].join("|")
}

function dispatch(action) {
  memoryState = reducer(memoryState, action)
  listeners.forEach((listener) => {
    listener(memoryState)
  })
}

function toast({
  dedupeKey,
  ...props
}) {
  const signature = dedupeKey || toastSignature(props)
  const now = Date.now()
  const previousAt = recentToastSignatures.get(signature) || 0
  if (now - previousAt < TOAST_DEDUPE_WINDOW_MS) {
    return {
      id: signature,
      dismiss: () => {},
      update: () => {},
    }
  }
  recentToastSignatures.set(signature, now)
  const id = genId()

  const update = (props) =>
    dispatch({
      type: "UPDATE_TOAST",
      toast: { ...props, id },
    })
  const dismiss = () => dispatch({ type: "DISMISS_TOAST", toastId: id })

  dispatch({
    type: "ADD_TOAST",
    toast: {
      ...props,
      id,
      open: true,
      onOpenChange: (open) => {
        if (!open) dismiss()
      },
    },
  })

  return {
    id: id,
    dismiss,
    update,
  }
}

function sanitizeToastTitle(title, fallback = "Notice") {
  const cleaned = String(title || "").trim()
  return cleaned || fallback
}

function sanitizeToastMessage(message) {
  return String(message || "").trim()
}

function mapTechnicalErrorMessage(text, fallback) {
  const normalized = String(text || "").trim()
  const lower = normalized.toLowerCase()
  if (!normalized) return fallback
  if (
    lower.includes("request body must be valid json")
    || lower.includes("bulk upload body must be valid json")
    || lower.includes("invalid multipart upload")
    || lower.includes("multipart/form-data")
    || lower.includes("missing multipart boundary")
  ) {
    return "Upload the spreadsheet as an Excel or CSV file."
  }
  if (
    lower.includes("could not read spreadsheet")
    || lower.includes("unsupported spreadsheet format")
    || lower.includes("invalid spreadsheet")
  ) {
    return "The selected file could not be read."
  }
  if (
    lower.includes("email delivery is not configured")
    || lower.includes("email channel is not enabled")
    || lower.includes("configure brevo")
    || lower.includes("configure smtp")
  ) {
    return "Email channel is not configured."
  }
  return normalized
}

function getErrorMessage(error, fallback = "We couldn't finish that action.") {
  const responseDetail = error?.response?.data?.detail
  const responseError = error?.response?.data?.error
  const raw = responseDetail ?? responseError ?? error?.message ?? ""

  if (Array.isArray(raw)) {
    const joined = raw
      .map((item) => {
        if (typeof item === "string") return item.trim()
        if (item && typeof item === "object") return String(item.msg || item.message || "").trim()
        return String(item || "").trim()
      })
      .filter(Boolean)
      .join(". ")
    return mapTechnicalErrorMessage(joined, fallback)
  }

  if (raw && typeof raw === "object") {
    const message = String(raw.message || raw.detail || "").trim()
    return mapTechnicalErrorMessage(message, fallback)
  }

  const text = String(raw || "").trim()
  if (!text) return fallback
  if (/network error/i.test(text)) return "The request could not reach the server. Please try again."
  if (/request failed with status code/i.test(text)) return fallback
  if (text.startsWith("{") || text.startsWith("[")) return fallback
  return mapTechnicalErrorMessage(text, fallback)
}

function showToast({
  type = "info",
  title = "",
  message = "",
  ...rest
}) {
  const variant = TYPE_TO_VARIANT[type] || "default"
  return toast({
    variant,
    title: sanitizeToastTitle(
      title,
      type === "error" ? "Action Failed" : type === "success" ? "Action Complete" : "Notice",
    ),
    description: sanitizeToastMessage(message),
    ...rest,
  })
}

function useToast() {
  const [state, setState] = React.useState(memoryState)

  React.useEffect(() => {
    listeners.push(setState)
    return () => {
      const index = listeners.indexOf(setState)
      if (index > -1) {
        listeners.splice(index, 1)
      }
    };
  }, [state])

  return {
    ...state,
    toast,
    showToast,
    dismiss: (toastId) => dispatch({ type: "DISMISS_TOAST", toastId }),
  };
}

export { useToast, toast, showToast, getErrorMessage }
