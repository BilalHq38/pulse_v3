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
      <AlertDialogContent
        className="w-[calc(100vw-2rem)] max-w-md gap-0 overflow-hidden rounded-xl border border-slate-200 bg-white p-0 text-slate-900 shadow-2xl sm:rounded-xl"
        data-testid="confirm-dialog"
      >
        <div className="p-6">
          <AlertDialogHeader className="space-y-2 text-left">
            <AlertDialogTitle className="text-lg font-semibold leading-6 text-slate-950">
              {dialog.title}
            </AlertDialogTitle>
            {dialog.description ? (
              <AlertDialogDescription className="text-sm leading-6 text-slate-600">
                {dialog.description}
              </AlertDialogDescription>
            ) : null}
          </AlertDialogHeader>
        </div>
        <AlertDialogFooter className="gap-2 border-t border-slate-100 bg-slate-50 px-6 py-4 sm:flex-row sm:justify-end sm:space-x-0">
          <AlertDialogCancel className="mt-0 rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-100 hover:text-slate-900">
            {dialog.cancelLabel}
          </AlertDialogCancel>
          <AlertDialogAction
            onClick={handleConfirm}
            className="rounded-lg bg-red-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-red-700 focus:ring-2 focus:ring-red-500 focus:ring-offset-2"
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
