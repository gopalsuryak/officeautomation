/**
 * Document Communication Draft Copy/Export Utilities
 */

(function() {
	'use strict';

	/**
	 * Copy text to clipboard with fallback support.
	 * @param {string} text - Text to copy
	 * @param {Element} buttonEl - Button element to provide feedback
	 */
	function copyToClipboard(text, buttonEl) {
		if (!text) {
			showFeedback(buttonEl, 'Nothing to copy', 'error');
			return;
		}

		// Try modern Clipboard API first
		if (navigator.clipboard && window.isSecureContext) {
			navigator.clipboard.writeText(text)
				.then(function() {
					showFeedback(buttonEl, 'Copied!', 'success');
				})
				.catch(function() {
					fallbackCopy(text, buttonEl);
				});
		} else {
			fallbackCopy(text, buttonEl);
		}
	}

	/**
	 * Fallback copy method using textarea selection.
	 * @param {string} text - Text to copy
	 * @param {Element} buttonEl - Button element to provide feedback
	 */
	function fallbackCopy(text, buttonEl) {
		var textarea = document.createElement('textarea');
		textarea.value = text;
		textarea.style.position = 'fixed';
		textarea.style.opacity = '0';
		document.body.appendChild(textarea);

		try {
			textarea.select();
			document.execCommand('copy');
			showFeedback(buttonEl, 'Copied!', 'success');
		} catch (err) {
			showFeedback(buttonEl, 'Copy failed', 'error');
		} finally {
			document.body.removeChild(textarea);
		}
	}

	/**
	 * Show feedback message on button temporarily.
	 * @param {Element} buttonEl - Button element
	 * @param {string} message - Feedback message
	 * @param {string} type - 'success' or 'error'
	 */
	function showFeedback(buttonEl, message, type) {
		if (!buttonEl) return;

		var originalText = buttonEl.textContent;
		var originalClass = buttonEl.className;

		buttonEl.textContent = message;
		buttonEl.className = 'btn btn-sm ' + (type === 'success' ? 'btn-success' : 'btn-danger');

		setTimeout(function() {
			buttonEl.textContent = originalText;
			buttonEl.className = originalClass;
		}, 2000);
	}

	/**
	 * Initialize copy buttons on the page.
	 */
	function initCopyButtons() {
		// Copy Subject button
		var copySubjectBtn = document.getElementById('copy-subject-btn');
		if (copySubjectBtn) {
			copySubjectBtn.addEventListener('click', function(e) {
				e.preventDefault();
				var subjectEl = document.getElementById('draft-subject');
				if (subjectEl) {
					copyToClipboard(subjectEl.value || subjectEl.textContent, copySubjectBtn);
				}
			});
		}

		// Copy Body button
		var copyBodyBtn = document.getElementById('copy-body-btn');
		if (copyBodyBtn) {
			copyBodyBtn.addEventListener('click', function(e) {
				e.preventDefault();
				var bodyEl = document.getElementById('draft-body');
				if (bodyEl) {
					copyToClipboard(bodyEl.value || bodyEl.textContent, copyBodyBtn);
				}
			});
		}
	}

	// Initialize when DOM is ready
	if (document.readyState === 'loading') {
		document.addEventListener('DOMContentLoaded', initCopyButtons);
	} else {
		initCopyButtons();
	}

	// Export for testing
	window.DocumentCommunication = {
		copyToClipboard: copyToClipboard,
		fallbackCopy: fallbackCopy,
		showFeedback: showFeedback
	};
})();
