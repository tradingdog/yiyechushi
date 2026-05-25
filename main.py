from image_generator import generate_recipe_assets_from_idea_file


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


if __name__ == "__main__":
    main()
