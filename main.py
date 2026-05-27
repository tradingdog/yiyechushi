from pathlib import Path

from script_logging import setup_script_logging

if __name__ == "__main__":
    setup_script_logging(__file__)

from image_generator import generate_recipe_assets_from_idea_file


def run_douyin_publish_tail_step(output_root: str) -> None:
    from tools.douyin_publish import publish_output_dir

    output_dir = Path(output_root)
    publish_dir = output_dir / "publish"
    if not publish_dir.exists() or not publish_dir.is_dir():
        print("当前输出目录下没有 publish 文件夹，跳过抖音发布收尾。")
        return

    print("当前主流程最后一步已完成，开始执行抖音发布收尾...")
    publish_output_dir(output_dir)
    print("抖音发布收尾已执行完成。")


def main() -> None:
    print("开始执行一页厨完整生成流程，请等待接口返回结果...")

    try:
        result = generate_recipe_assets_from_idea_file(idea_file_name="dish_name.txt")
    except Exception as exc:
        print(f"运行失败：{exc}")
        raise SystemExit(1) from exc

    print("图片生成完成")
    print(f"本次创意输入：{result['dish_idea']}")
    print(f"本次最终菜名：{result['dish_name']}")
    print(f"输出根目录：{result['output_root']}")
    print(f"抖音图文标题文件：{result['publish_title_file']}")
    print(f"抖音图文描述文件：{result['publish_description_file']}")
    if result.get("publish_selection_report_file"):
        print(f"publish 评分报告：{result['publish_selection_report_file']}")
    if result.get("publish_selection_summary_file"):
        print(f"publish 摘要报告：{result['publish_selection_summary_file']}")
    for page in result["guide_pages"]:
        print(f"图解{page['page_number']:02d}：{page['page_name']}")
        print(f"图解文案文件：{page['text_file']}")
        print(f"图解 prompt 文件：{page['prompt_file']}")
        for saved_file in page["saved_files"]:
            print(f"已保存图解：{saved_file}")
    print(f"封面 prompt 文件：{result['cover_prompt_file']}")
    for saved_file in result["cover_saved_files"]:
        print(f"已保存封面：{saved_file}")
    for selected_file in result.get("publish_selected_files", []):
        print(f"已选入 publish：{selected_file}")

    try:
        run_douyin_publish_tail_step(result["output_root"])
    except Exception as exc:
        print(f"前序生成已完成，但抖音发布收尾失败：{exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
