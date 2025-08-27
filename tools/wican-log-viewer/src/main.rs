use crossbeam_channel::{unbounded, Receiver};
use std::{net::UdpSocket, thread, time::Duration};
use std::io::Write; // needed for writeln!(File,...)
use clap::Parser;
use regex::Regex;
use chrono::Local;

#[derive(Parser, Debug)]
#[command(author, version, about="WiCAN UDP Log Viewer", long_about=None)]
struct Args {
    #[arg(long, default_value_t=5000, help="UDP listen port")] 
    port: u16,
    #[arg(long, default_value_t=10000, help="Max lines kept in memory")] 
    max_lines: usize,
    #[arg(long, default_value_t=2000, help="Prune batch when over max")] 
    prune_batch: usize,
}

#[derive(Clone)]
struct LogLine {
    raw: String,
    level: char,
    ts_ms: u64,
    task: String,
    tag: String,
    msg: String,
}

fn spawn_udp(port: u16, tx: crossbeam_channel::Sender<LogLine>) {
    thread::spawn(move || {
        let bind = ("0.0.0.0", port);
        let sock = UdpSocket::bind(bind).expect("Failed to bind UDP port");
        let mut buf = [0u8; 2048];
        loop {
            if let Ok((n, _src)) = sock.recv_from(&mut buf) {
                if n == 0 { continue; }
                let raw = String::from_utf8_lossy(&buf[..n]).trim_end_matches(['\n','\r']).to_string();
                if raw.is_empty() { continue; }
                let parsed = parse_line(&raw);
                let _ = tx.send(parsed);
            }
        }
    });
}

fn parse_line(raw: &str) -> LogLine {
    // Expected: [timestamp][L][task][tag] message
    let mut rest = raw;
    let mut ts_ms = 0u64; let mut level='?'; let mut task="".to_string(); let mut tag="".to_string();
    if let Some(end) = rest.find(']') {
        if rest.starts_with('[') { ts_ms = rest[1..end].parse().unwrap_or(0); }
        rest = &rest[end+1..];
    }
    if let Some(end) = rest.find(']') {
        if rest.starts_with('[') { level = rest[1..end].chars().next().unwrap_or('?'); }
        rest = &rest[end+1..];
    }
    if let Some(end) = rest.find(']') {
        if rest.starts_with('[') { task = rest[1..end].to_string(); }
        rest = &rest[end+1..];
    }
    if let Some(end) = rest.find(']') {
        if rest.starts_with('[') { tag = rest[1..end].to_string(); }
        rest = &rest[end+1..];
    }
    let msg = rest.trim_start().to_string();
    LogLine { raw: raw.to_string(), level, ts_ms, task, tag, msg }
}

struct App {
    rx: Receiver<LogLine>,
    lines: Vec<LogLine>,
    paused: bool,
    min_level: char,
    filter_text: String,
    filter_regex: Option<Regex>,
    search: String,
    max_lines: usize,
    prune_batch: usize,
    last_stats: (usize, usize), // (total lines, shown)
    export_status: Option<(String,std::time::Instant)>,
}

impl App {
    fn new(rx: Receiver<LogLine>, max_lines: usize, prune_batch: usize) -> Self {
        Self { rx, lines: Vec::new(), paused: false, min_level: 'I', filter_text: String::new(), filter_regex: None, search: String::new(), max_lines, prune_batch, last_stats: (0,0), export_status: None }
    }
}

fn level_order(c: char) -> u8 { match c { 'E'=>0,'W'=>1,'I'=>2,'D'=>3,'V'=>4,_=>5 } }
fn color_for(l: char) -> egui::Color32 { match l { 'E'=>egui::Color32::from_rgb(255,100,100), 'W'=>egui::Color32::from_rgb(255,180,80), 'I'=>egui::Color32::from_rgb(150,220,120), 'D'=>egui::Color32::from_rgb(120,180,255), 'V'=>egui::Color32::from_gray(170), _=>egui::Color32::WHITE } }

impl eframe::App for App {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        // Force dark theme every frame (cheap & guarantees black background)
    if ctx.style().visuals.dark_mode == false {
            ctx.set_visuals(egui::Visuals::dark());
        }
        // Optionally tweak background to full black
        let mut style = (*ctx.style()).clone();
        style.visuals.panel_fill = egui::Color32::BLACK;
        style.visuals.window_fill = egui::Color32::BLACK;
        ctx.set_style(style);

        if !self.paused {
            for _ in 0..500 { // limit per frame to keep UI responsive
                match self.rx.try_recv() {
                    Ok(l) => self.lines.push(l),
                    Err(_) => break,
                }
            }
            if self.lines.len() > self.max_lines { self.lines.drain(0..self.prune_batch.min(self.lines.len())); }
        }

        egui::TopBottomPanel::top("top").show(ctx, |ui| {
            ui.horizontal_wrapped(|ui| {
                ui.label(format!("Recv: {}", self.lines.len()));
                for (btn,lvl) in [ ("E",'E'), ("W",'W'), ("I",'I'), ("D",'D'), ("V",'V') ] { if ui.selectable_label(self.min_level==lvl, btn).clicked() { self.min_level = lvl; } }
                if ui.button(if self.paused {"Resume"} else {"Pause"}).clicked() { self.paused = !self.paused; }
                if ui.button("Clear").clicked() { self.lines.clear(); }
                ui.label("Tag/Task regex:");
                if ui.text_edit_singleline(&mut self.filter_text).changed() {
                    self.filter_regex = if self.filter_text.is_empty() { None } else { Regex::new(&self.filter_text).ok() };
                }
                ui.label("Search:"); ui.text_edit_singleline(&mut self.search);
                if ui.button("Export").clicked() {
                    let fname = format!("wican_logs_{}.txt", Local::now().format("%Y%m%d_%H%M%S"));
                    match std::fs::File::create(&fname) {
                        Ok(mut f) => {
                            let mut count = 0usize;
                            for l in self.lines.iter().filter(|l| level_order(l.level) <= level_order(self.min_level))
                                .filter(|l| {
                                    if let Some(re) = &self.filter_regex { if !(re.is_match(&l.tag) || re.is_match(&l.task)) { return false; } }
                                    if !self.search.is_empty() && !l.raw.to_lowercase().contains(&self.search.to_lowercase()) { return false; }
                                    true
                                }) {
                                let _ = writeln!(f, "{}", l.raw);
                                count += 1;
                            }
                            let msg = format!("Exported {} lines -> {}", count, fname);
                            self.export_status = Some((msg, std::time::Instant::now()));
                        },
                        Err(e) => {
                            self.export_status = Some((format!("Export failed: {e}"), std::time::Instant::now()));
                        }
                    }
                }
                if let Some((msg, t)) = &self.export_status {
                    // Show message for 8 seconds
                    if t.elapsed() < Duration::from_secs(8) {
                        ui.colored_label(egui::Color32::LIGHT_GREEN, msg);
                    }
                }
            });
        });

        egui::CentralPanel::default().show(ctx, |ui| {
            egui::ScrollArea::vertical().stick_to_bottom(true).show(ui, |ui| {
                for l in self.lines.iter().filter(|l| level_order(l.level) <= level_order(self.min_level)) {
                    if let Some(re) = &self.filter_regex { if !(re.is_match(&l.tag) || re.is_match(&l.task)) { continue; } }
                    if !self.search.is_empty() && !l.raw.to_lowercase().contains(&self.search.to_lowercase()) { continue; }
                    ui.colored_label(color_for(l.level), &l.raw);
                }
            });
        });

        ctx.request_repaint_after(Duration::from_millis(33));
    }
}

fn main() -> eframe::Result<()> {
    let args = Args::parse();
    let (tx, rx) = unbounded();
    spawn_udp(args.port, tx);
    let app = App::new(rx, args.max_lines, args.prune_batch);
    let native_options = eframe::NativeOptions::default();
    eframe::run_native("WiCAN Log Viewer", native_options, Box::new(|_cc| Box::new(app)))
}
