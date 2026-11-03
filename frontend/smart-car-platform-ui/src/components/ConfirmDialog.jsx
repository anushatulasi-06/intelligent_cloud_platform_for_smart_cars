// Yes/no confirmation built on Modal. Use `danger` for destructive actions.
//
// Usage:
//   <ConfirmDialog
//     open={!!pending}
//     title="Activate model"
//     message={`Make ${pending?.version_label} the active model?`}
//     confirmLabel="Activate"
//     danger={false}
//     busy={saving}
//     onConfirm={doActivate}
//     onCancel={() => setPending(null)}
//   />
import Modal from './Modal.jsx';

export default function ConfirmDialog({
  open, title = 'Are you sure?', message, confirmLabel = 'Confirm', cancelLabel = 'Cancel',
  danger = false, busy = false, onConfirm, onCancel,
}) {
  return (
    <Modal
      open={open}
      title={title}
      onClose={onCancel}
      footer={
        <>
          <button className="btn" onClick={onCancel} disabled={busy}>{cancelLabel}</button>
          <button className={`btn ${danger ? 'btn-danger' : 'btn-primary'}`} onClick={onConfirm} disabled={busy}>
            {busy ? 'Working...' : confirmLabel}
          </button>
        </>
      }
    >
      <p>{message}</p>
    </Modal>
  );
}
