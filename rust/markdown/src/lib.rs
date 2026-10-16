//! Markdown parsing for the browser. The caller MUST sanitize the returned HTML.
//!
//! Rust owns both buffers: JavaScript writes UTF-8 into the input buffer and
//! copies HTML out before the next call. No raw pointer is dereferenced in Rust.
#![deny(unsafe_op_in_unsafe_fn)]

use comrak::{markdown_to_html, Options};

pub fn render(markdown: &str) -> String {
    let mut options = Options::default();
    options.extension.strikethrough = true;
    options.extension.table = true;
    options.extension.autolink = true;
    options.extension.tasklist = true;
    // Match the existing Markdown renderer. DOMPurify remains mandatory before
    // inserting this output into the DOM, including raw HTML and link targets.
    options.render.r#unsafe = true;
    markdown_to_html(markdown, &options)
}

#[cfg(target_arch = "wasm32")]
mod wasm {
    use std::cell::RefCell;

    #[derive(Default)]
    struct Buffers {
        input: Vec<u8>,
        output: String,
    }

    thread_local! {
        static BUFFERS: RefCell<Buffers> = RefCell::new(Buffers::default());
    }

    #[no_mangle]
    pub extern "C" fn markdown_input(length: usize) -> *mut u8 {
        BUFFERS.with_borrow_mut(|buffers| {
            buffers.input.resize(length, 0);
            buffers.input.as_mut_ptr()
        })
    }

    #[no_mangle]
    pub extern "C" fn markdown_render() -> *const u8 {
        BUFFERS.with_borrow_mut(|buffers| {
            let markdown = String::from_utf8_lossy(&buffers.input);
            buffers.output = super::render(&markdown);
            buffers.output.as_ptr()
        })
    }

    #[no_mangle]
    pub extern "C" fn markdown_output_len() -> usize {
        BUFFERS.with_borrow(|buffers| buffers.output.len())
    }
}

#[cfg(test)]
mod tests {
    use super::render;

    #[test]
    fn preserves_unicode_code_fences_and_citation_markers() {
        let html = render("# Été 🐘\n\nTexte **fort** [[sources:1,2]].\n\n```2ai-preview\n{\"title\":\"Idée\"}\n```\n");
        assert!(html.contains("<h1>Été 🐘</h1>"));
        assert!(html.contains("<strong>fort</strong> [[sources:1,2]]"));
        assert!(html.contains("class=\"language-2ai-preview\""));
        assert!(html.contains("{&quot;title&quot;:&quot;Idée&quot;}"));
    }

    #[test]
    fn supports_the_existing_gfm_features() {
        let html = render("| A | B |\n|---|---|\n| 1 | 2 |\n\n- [x] Fait\n- [ ] À faire\n\n~~barré~~ https://example.com\n");
        assert!(html.contains("<table>"));
        assert!(html.contains("type=\"checkbox\""));
        assert!(html.contains("<del>barré</del>"));
        assert!(html.contains("href=\"https://example.com\""));
    }

    #[test]
    fn raw_html_is_left_for_the_browser_sanitizer() {
        assert!(render("<img src=x onerror=alert(1)>").contains("onerror="));
    }

    #[test]
    fn handles_empty_and_unfinished_streaming_markdown() {
        assert_eq!(render(""), "");
        assert!(render("```rust\nlet x =").contains("<pre><code"));
        assert!(render("**Réponse en cours").contains("Réponse en cours"));
    }
}
