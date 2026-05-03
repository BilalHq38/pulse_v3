const React = require('react');
const { createRoot } = require('react-dom/client');
const { act } = React;
const { useConfirmDialog } = require('./use-confirm-dialog');

global.IS_REACT_ACT_ENVIRONMENT = true;
window.PointerEvent = window.PointerEvent || MouseEvent;
HTMLElement.prototype.hasPointerCapture = HTMLElement.prototype.hasPointerCapture || (() => false);
HTMLElement.prototype.releasePointerCapture = HTMLElement.prototype.releasePointerCapture || (() => {});
HTMLElement.prototype.scrollIntoView = HTMLElement.prototype.scrollIntoView || (() => {});

function Harness({ onConfirm }) {
  const { requestConfirmation, confirmDialog } = useConfirmDialog();
  return (
    <div>
      <button
        type="button"
        data-testid="open-confirm"
        onClick={() => requestConfirmation({
          title: 'Delete conversation',
          description: 'This removes the selected chat history.',
          confirmLabel: 'Delete',
          cancelLabel: 'Cancel',
          onConfirm,
        })}
      >
        Open
      </button>
      {confirmDialog}
    </div>
  );
}

async function tick() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function renderHarness(onConfirm = jest.fn()) {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(<Harness onConfirm={onConfirm} />);
  });
  return { container, root, onConfirm };
}

afterEach(() => {
  document.body.innerHTML = '';
  jest.clearAllMocks();
});

test('confirmation dialog renders clearly and Cancel closes it', async () => {
  const { container, root } = await renderHarness();

  await act(async () => {
    container.querySelector('[data-testid="open-confirm"]').dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
  await tick();

  const dialog = document.querySelector('[data-testid="confirm-dialog"]');
  expect(dialog).not.toBeNull();
  expect(dialog.textContent).toContain('Delete conversation');
  expect(dialog.textContent).toContain('This removes the selected chat history.');
  expect(dialog.className).toContain('max-w-md');
  expect(dialog.className).toContain('bg-white');

  const cancel = Array.from(document.querySelectorAll('button')).find((button) => button.textContent === 'Cancel');
  await act(async () => {
    cancel.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
  await tick();

  expect(document.querySelector('[data-testid="confirm-dialog"]')).toBeNull();
  await act(async () => root.unmount());
});

test('confirmation dialog Delete calls the confirm handler once', async () => {
  const onConfirm = jest.fn();
  const { container, root } = await renderHarness(onConfirm);

  await act(async () => {
    container.querySelector('[data-testid="open-confirm"]').dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
  await tick();

  const confirm = Array.from(document.querySelectorAll('button')).find((button) => button.textContent === 'Delete');
  await act(async () => {
    confirm.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
  await tick();

  expect(onConfirm).toHaveBeenCalledTimes(1);
  expect(document.querySelectorAll('[data-testid="confirm-dialog"]')).toHaveLength(0);
  await act(async () => root.unmount());
});
