# Glossing rules

How the program turns any Japanese subtitle line into karaoke glosses, with no
per-episode tuning. Each rule says **what** is shown and **who decides**:
code (deterministic), dictionary (JMdict), LLM (fast pass), verifier (slow
second look at suspicious words only).

## 1. What the viewer sees

| # | Situation | Shown as | Decided by | Example |
|---|---|---|---|---|
| 1.1 | Content word (noun, verb, adjective, adverb, pronoun) | **UPPERCASE** English, the *shortest* gloss that keeps the meaning: 1 word preferred, max 2 words / ~14 letters | LLM, from dictionary senses + context | 市販 → STORE-BOUGHT, not PUTTING-ON-THE-MARKET · ずっと → ALWAYS |
| 1.2 | Conjugated word | gloss of the **root** + the part that **changed from the dictionary form** in lowercase romaji, glued with `-`. Dictionary forms and stems are not split | code (UniDic dictionary form) splits; LLM glosses the root | 呼んでる → CALL-n-deru · 大きかった → BIG-ka-tta · なった → BECOME-tta · 駆け上がる → DASH-UP (not DASH-UP-garu) |
| 1.3 | Grammar: particles, copula, auxiliaries, sentence-final particles, する / いる / くれる / やる / みる / しまう / おく after て, the negative ending glued to a verb (分からん, 死んでない) | lowercase romaji, never translated | code | ga, ni, -te-iru, yo, ne, ka, ga-na |
| 1.4 | Honorific prefix お/ご, name suffixes さん/ちゃん/君/様 | lowercase romaji glued to the word | code | お父さん → o-FATHER-san · 大樹君 → Taiju-kun |
| 1.4b | 姉/兄 glued to a name or a kana nickname | the address -nee / -nii: a romaji suffix like -san, read ねえ/にい (not あね); the word before it is a name. A kana word UniDic breaks into symbols (み\|ゃー) is one word | code | みゃー姉 → Mya-nee (was "mi yaa Ane") |
| 1.5 | Names (people, pets, places in the story) | romaji, **First-letter capital, long vowels dropped** (as English subtitles write them), never translated; spelling from the English subtitles when available. **Consistent per episode**: marked once = a name everywhere, when code found it (furigana, UniDic, English subs) or the word is not in the dictionary; the LLM calling a dictionary word a name (花 Hana) holds for that line only (elsewhere 花 is a flower). **Series**: `--names-from` the other episodes' subtitles, since furigana is given once per series (夏音(かのん) in Ep 5 only) | code (furigana, UniDic, English subs) + LLM / verifier can declare a name (`@`) | 杠 → Yuzuriha · 千空 → Senku · クスノキ → Kusunoki |
| 1.5b | Not names (Whisper of the Heart, 2026-10-15) | a title or nickname that is a dictionary word, written capitalised mid-sentence by the English subtitles (男爵 → "the Baron"), is translated everywhere (BARON); katakana emphasis on an ordinary word is that word (カゲキ → RADICAL, ヤダ → NO-WAY). A name written in kana, split or stretched by a shout, is the known name (つき\|しまーっ → Tsukishima; 3+ kana so おー is not 王; a long vowel inside counts, so ほしいの is not 星野). Never names: shouts cut by a glottal stop (フンッ, あっ), single kana, anything but a noun | code (English check, kana match) + LLM rule | code (English check, kana match) + LLM rule | 男爵 was Danshaku, つきしまーっ was Tsuki shimaa |
| 1.6 | Interjections, fillers, sounds | lowercase romaji, never translated | code (part of speech) + LLM may keep | おお → oo · ハァハァ → haa-haa · えっ → e |
| 1.7 | Numbers + counters | **digits**; a number split over tokens is **one value** on its first token (百\|億 → 10-BILLION, ""); round big values as words (10-THOUSAND, 100-MILLION, 10-BILLION). Counter glossed in the singular by the LLM, made **plural** by code after a number ≠ 1 or HOW-MANY/SEVERAL; singular in dates (era word before the number, or a number+月 in the line) | code (value, plural) + LLM (counter) | 三百円 → 300 YEN · 五年 → 5 YEARS · 一年 → 1 YEAR · 百億 → 10-BILLION · 十月五日 → 10 MONTH 5-DAY |
| 1.8 | Loanwords from English (katakana) | the English source word | dictionary (JMdict stores the origin) | アルコール → ALCOHOL |
| 1.9 | Colloquial / slang forms | gloss of the standard form; romaji still shows what is *said* | code normalises (table), LLM glosses | ひでえ (= ひどい) → TERRIBLE · すげえ → AMAZING · ねえ (= ない) → NOT |
| 1.10 | Idioms | still word-by-word if each word means something (that is what teaches), otherwise the idiom's sense on its main word | LLM | 骨が折れる → BONE ga BREAK · 思いっきり → ALL-OUT |
| 1.11 | Suffixes (UniDic 接尾辞) | **meaning-carrying** ones glossed like a word, glued with `-`; **grammar-like** ones (的, 化, 性, さ, み, げ) and **honorifics** stay romaji (1.4); **plurals** (ら, たち, ども, 方 after a pronoun) fold into the word before: its plural gloss, the suffix keeps its timing but shows no text. After a name or a word kept in Japanese, a plural stays romaji | code (which kind, plural fold) + LLM (the gloss, or `=` if it adds nothing) | 市販品 → STORE-BOUGHT-GOODS · 七百歳 → 700-YEARS-OLD · 酸 → ACID (not the honorific -san) · 科学的 → SCIENCE-teki · 俺ら → WE · お前ら → YOU-ALL · 千空たち → Senku-tachi |

## 2. Understanding the line correctly (before glossing)

| # | Problem | Rule | Example from Ep 1 |
|---|---|---|---|
| 2.1 | Tokenizer doesn't know colloquial forms and splits them into nonsense | **Normalise for analysis only**: a table of same-length spellings (思いっきし→思いっきり, ひでえ→ひどい, すげえ→すごい, ありゃ→あれば, ねえ→ない, …). Part of speech and dictionary form come from the normalised text; timing and romaji from what is actually said. Same length = character positions and audio timing stay valid. | 思いっきし was read as 思いっ + きし ("thought" + "river bank") |
| 2.2 | Ambiguous contraction (ありゃ = あれば "if there is" *or* あれは "that is") | try the table entry, let the LLM confirm against the English line; if unsure, keep romaji | 酒のアルコールがありゃ |
| 2.3 | A hiragana "noun" at the end of a sentence after a predicate | almost always a sentence-final particle misread as a noun → grammar (1.3) | ひでえ**がな** was read as 仮名 "kana" · re-tagged words are never names (よい\|せっ was "Set") |
| 2.4 | Reading fixes (お母さん → okaasan) must not merge tokens | apply readings per token, keep お / 母 / さん separate so 1.4 works | お父さん became one token |
| 2.5 | Subtitle furigana over several dictionary words: a name (千+空 → Senku) or just a reading aid? | **reading aid, not a name**, when the ruby reads real words the dictionary found (図書\|館, 夏期\|集中\|講座; voicing ignored: 開放日 かいほうび): one word, translated (LIBRARY); when it reads only the tail (二十冊(さつ), 市立図書館(としょかん)) only the tail is one word; a name + suffix (聖司\|君) keeps its tokens; a merged word JMdict knows with that reading (洗濯物, 私達, 何度) is a word, not a name. Still a name: single kanji read one by one, unknown to JMdict (千空) | Whisper of the Heart (Erai-raws ruby on hard words): 夏期集中講座 → Kakishuchukoza, 図書館 → Toshokan ×12, ~60 false names |
| 2.6 | だって read as the verb 立つ (だっ + て) | one token だって: the conjunction (BUT / BECAUSE) at the start of a sentence, else the particle "even" | だって くれるんだもの: くれる was taken as the auxiliary after て and left as romaji |
| 2.7 | UniDic cuts compounds into short units (市\|立, 図書\|室, 日曜\|日) | neighbouring nouns and meaning suffixes that JMdict lists together as one common word with the same reading are one word (MUNICIPAL). Never across a pause, names, numbers (1.7), honorific/romaji suffixes (1.11) or plural suffixes | 市立 was CITY -PUBLIC |
| 2.8 | A verb stem before 中 (休み\|中) | a noun: HOLIDAY -DURING, not HOLIDAY-mi | 休み中 |

## 3. Context the LLM gets

1. The line, the 3 previous lines, dictionary senses per word (shortest first).
2. **English subtitles for the same moment** (optional but recommended):
   - Source, in order of preference: an English track inside the video file → official YouTube CC (yt-dlp) → a subtitle site.
   - Synced to the audio with the same VAD method as the Japanese subs, then paired by **time overlap** (an English line can cover 2 Japanese lines and vice versa).
   - Used as a *hint* for sense, names, idioms and contractions, never copied (English word order and localisation differ).
   - Name signal: a Capitalised English word matching a token's romaji (Senku ~ senkuu) → name (1.5) with that spelling.
3. Without English subs the pipeline still runs; quality is just lower on context-heavy lines.

## 4. Checking (replaces most human review)

1. Code flags suspicious words as today: wrong number value, non-English word, gloss outside every dictionary sense, gloss longer than the length budget.
2. **Verifier**: only flagged words (~8 %) go to a second LLM call with these rules, the dictionary senses and the English line. It writes a one-sentence analysis first, then keeps or replaces the gloss.
   On a sense doubt alone (`unusual`) it may not make the gloss more than 3 letters longer (AWESOME → TERRIBLE ok, ALWAYS → CONTINUOUSLY blocked): without new information the same model drifts back to long dictionary wording.
3. The review sheet shows the verifier's decisions; a human only looks if they want to. `--fixes` stays for manual overrides.
4. "Outside the dictionary" alone is no longer a problem: in Ep 1 the LLM was right in most of those (連中 → FOLKS, 土器 → POTTERY, 地道 → GRINDING, 呼ん → CALL). It is escalated only when the verifier or the English line disagrees.

## 5. Decisions (2026-10-14)

1. Names: First-capital romaji, long vowels dropped: Senku, Taiju, Yuzuriha.
2. Numbers: digits; a split number is one value (10-BILLION); counters plural after a number ≠ 1 (5 YEARS).
3. English subtitles: downloaded per episode from a subtitle site (site to be agreed).
4. Verifier: the same Qwen3.5-9B; it writes a short analysis before its verdict.

## 6. Results on Dr. Stone Ep 1 (first run of these rules)

- 1,002 glossed words; verifier checked 83, changed 35; 16 lines / 25 words left on the optional review sheet.
- Verifier is reliable on mechanical errors (IDOT → IDIOT, SLOUGHLY → SLUGGISHLY, YUZURUHA → name,
  OHHHH → kept in Japanese) and roughly a coin flip on pure sense doubts (でかした "well done!" → GROW-BIG
  was wrong). English subtitles are the missing information for those.
- Known gaps: meaning-carrying suffixes are shown as romaji (市販品 → STORE-BOUGHT-hin), 俺ら → I-ra (fixed by 1.11).

## 7. Rules from comparing with a reference script (2026-10-14)

Three Opus agents wrote a reference karaoke script for all of Dr. Stone Ep 1 in the program's own
token slots (`work/dr_stone_01/gold/`, compared with `karaoke compare`). The program already agreed
on 96 % of decisions (English / romaji / name) and 95 % of endings; the differences grouped into
these general rules. Word *choice* differences were mostly synonyms (ALL vs EVERYTHING) and are not
chased one by one.

| # | Situation | Rule | Decided by | Example |
|---|---|---|---|---|
| 7.1 | Same word, different glosses in one episode | one gloss per dictionary form + part of speech: the most frequent one (first seen wins a tie); names: the most frequent spelling of the surface | code (after the verifier, before `--fixes`) | 酒 DRINK/ALCOHOL/LIQUOR → LIQUOR · 大樹 Taiki → Taiju |
| 7.2 | Tags UniDic gets wrong in dialogue | filler あの/その/この before a noun is the demonstrative; ねえ/ない tagged as a final particle after a noun or も/は/が is the adjective ない | code | あの暗闇 → THAT DARKNESS · 機材もねえ → EQUIPMENT mo NOT |
| 7.3 | Laughs, pants, shouts tagged as adverb / noun / verb / prefix | romaji, never translated: kana only and an interjection, ending in a glottal stop (not verbs: なっ), one kana repeated (おおお), or (not nouns) a small vowel / long mark. お/ご not before a word is not an honorific. **Not** the first half of a mimetic adverb the tokenizer split before と with no pause (ぱっ\|と, さっ\|と, ちーっ\|と): that is a word, glossed, its と shown as -tto | code | ハァハァ → haahaa (was PANT) · フッ → fu · おおおおお → o o ooo · ちーっと → A-LITTLE tto (not a sound) |
| 7.4 | ない standing on its own vs glued | the adjective ない on its own is NOT (じゃねえ, わけねえ, たくない); the negative ending glued to a verb or after て/で stays romaji | code | やるじゃねえか → DO-ja NOT ka · 死んでない → DIE-n-de-nai |
| 7.5 | Potential verbs (頼れる, 終われる) | glossed as the plain verb, the potential ending shown (RELY-re, END-reru); not when the -eru verb is a common word of its own (届ける, 続ける) | code (JMdict) + LLM | 頼れん → RELY-re-n |
| 7.6 | Kana-only verbs | show their changed ending like kanji verbs | code | かければ → POUR-re-ba (was PUT-ba) |
| 7.7 | Pause before an auxiliary | no glue across a space or punctuation | code | 分からん だが → … da-ga |
| 7.8 | Pronoun as object | ME/US/HIM/HER/THEM before を or に | code | 俺を → ME o · 俺らを → US o |
| 7.9 | English written in the Japanese subtitles | shown as itself, uppercase, pieces rejoined | code | What's happening → WHAT'S HAPPENING (was "wha t shappenin") |
| 7.10 | Katakana loanword equal to its romaji | still a translation | code | ゼロ → ZERO (was zero) |
| 7.11 | Conjunctions | glossed | LLM | しかし → BUT |
| 7.12 | Noun before する | glossed as an English verb | LLM (hint "before する") | 説明しねえ → EXPLAIN shi nee |
| 7.13 | Katakana plants, animals, substances | ordinary nouns with their English name, never names | LLM | ヨモギ → MUGWORT |
| 7.14 | Same word, many lines (Claude glosser, `--claude-glossary`) | first one default gloss per distinct word (dictionary form + part of speech), chosen from up to 3 lines where it is used and their English; the line pass shows it as "episode gloss" and keeps it unless the line uses another sense | LLM (two passes) | バッタリ chosen once, from all its lines |

Kept by the user's choice although the reference differs: よう / そう / みたい are translated (LIKE, SEEMS).

## 8. The user's own vocabulary (2026-10-17)

Kept for the user: `~/.karaoke-subs/` (or `--user-dir`). One known list for all series; `learn` groups the questions by series (8.4).

| # | Situation | Shown as | Decided by | Example |
|---|---|---|---|---|
| 8.1 | Word in `known_words.txt` (one `Japanese|romaji` per line, the Japanese a dictionary form or its hiragana reading; sorted by the Japanese; binary search). `karaoke learn --import-anki DECK` adds every word of the deck's studied cards (not new, not suspended; Jlab's lemma field) | romaji, like a word kept in Japanese | code | `石` known → 石になった → ishi ni BECOME-tta |
| 8.2 | Every translated word | counted per episode in `words.json` (a re-run of an episode replaces its count) with its gloss and an example line | code | 石: seen {ep1: 3, ep2: 2} |
| 8.3 | Word heard 5 times in total | asked in the Words section of `karaoke ui` or in `karaoke learn` (terminal), same questions and files: word, romaji, reading, example, then the gloss; the user says if they knew it. Yes → added to `known_words.txt`. No → asked again after 10 more times, then 20, 40, 80 (the last step repeats) | user | |
| 8.4 | Every series that has words counted. Series = the file name without `[tags]` and a trailing ` - 01` / `_01` (`series_of`) | `learn` has a section per series: its words not asked yet, most often heard first, 30 per run (`--limit`). Each shows romaji, reading, example, then the gloss. A word already asked in the heard-often list is skipped here. Yes → known. No → never asked again, in any section | user | Watashi ni Tenshi ga Maiorita! → 火 asked once |

`--no-known` on `time` turns both off (runs scored with `compare` should use it).
