import { useCallback, useMemo, useState } from 'react';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';

const DEFAULT_DIALOG = {
  open: false,
  title: '',
  description: '',
  confirmLabel: 'Confirm',
  cancelLabel: 'Cancel',
  onConfirm: null,
};

export function useConfirmDialog() {
  const [dialog, setDialog] = useState(DEFAULT_DIALOG);

  const closeConfirmDialog = useCallback(() => {
    setDialog(DEFAULT_DIALOG);
  }, []);

  const requestConfirmation = useCallback((config) => {
    setDialog({
      open: true,
      title: String(config?.title || 'Confirm action').trim(),
      description: String(config?.description || '').trim(),
      confirmLabel: String(config?.confirmLabel || 'Continue').trim(),
      cancelLabel: String(config?.cancelLabel || 'Cancel').trim(),
      onConfirm: typeof config?.onConfirm === 'function' ? config.onConfirm : null,
    });
  }, []);

  const handleConfirm = useCallback(() => {
    const confirmAction = dialog.onConfirm;
    closeConfirmDialog();
    if (confirmAction) {
      void Promise.resolve(confirmAction());
    }
  }, [closeConfirmDialog, dialog.onConfirm]);

  const confirmDialog = useMemo(() => (
    <AlertDialog open={dialog.open} onOpenChange={(open) => { if (!open) closeConfirmDialog(); }}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{dialog.title}</AlertDialogTitle>
          <AlertDialogDescription>{dialog.description}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>{dialog.cancelLabel}</AlertDialogCancel>
          <AlertDialogAction
            onClick={handleConfirm}
            className="bg-red-600 text-white hover:bg-red-500 focus:ring-red-500"
          >
            {dialog.confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  ), [closeConfirmDialog, dialog.cancelLabel, dialog.confirmLabel, dialog.description, dialog.open, dialog.title, handleConfirm]);

  return {
    requestConfirmation,
    closeConfirmDialog,
    confirmDialog,
  };
}
